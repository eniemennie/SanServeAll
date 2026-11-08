/**
 * Direct-to-laptop ESC/POS printing via the Web Bluetooth API (Task
 * tracker, 2026-11-08: PT-210 printer integration -- "add it alongside
 * RawBT" option). This is an ADDITION next to the existing RawBT path
 * (apps/pos/printing.py, the "Print via Bluetooth Printer (RawBT)"
 * button) -- not a replacement. RawBT stays as the proven fallback,
 * since this path has real, documented unknowns:
 *
 *   - Web Bluetooth only exists in Chrome/Edge (desktop or Android) --
 *     there is no Safari or Firefox support at all.
 *   - The GATT service/characteristic UUIDs below come from the
 *     unofficial, reverse-engineered goojprt-pt210-sdk
 *     (https://pypi.org/project/goojprt-pt210-sdk/), whose own docs say
 *     the protocol was captured from ONE tested unit and that "other
 *     units or firmware may differ." Both the primary (ISSC/Microchip
 *     "transparent UART") and the alternate (generic HM-10-style)
 *     UUID sets are tried, since different PT-210 firmware revisions
 *     may expose either.
 *   - This genuinely cannot be exercised by an automated test or in a
 *     sandbox with no Bluetooth hardware -- it has to be tried against
 *     the real printer.
 *
 * Chunk size (182 bytes) and the inter-chunk delay (40ms) mirror that
 * SDK's own documented pacing for its BLE transport (the PT-210 sits
 * behind a UART module with no flow control, so pacing is open-loop on
 * the sender's side either way).
 *
 * Known flakiness (found 2026-11-08, confirmed working against the
 * real printer): the first print after opening the page succeeds, but
 * a later attempt sometimes hangs forever at "Connecting..." with no
 * error, and the only fix found was power-cycling the printer. Most
 * likely cause -- unconfirmed, since this is unofficial/reverse-
 * engineered hardware with no documented reconnect behavior: the
 * browser returns the SAME BluetoothDevice object once permission is
 * granted, and if the printer's side didn't cleanly tear down the
 * previous GATT connection, a fresh connect() call can simply never
 * resolve. Two mitigations below: a timeout on the connect/print steps
 * so a hang becomes a clear error instead of an indefinitely frozen
 * button, and disconnecting defensively before every new attempt.
 * Neither can fix a printer-side issue outright -- if it still hangs,
 * powering the printer off and on remains the real fix.
 */
(function (window) {
  "use strict";

  var BLE_SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455";
  var BLE_SERVICE_UUID_ALT = "0000ff00-0000-1000-8000-00805f9b34fb";
  var BLE_WRITE_CHAR_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3";
  var BLE_WRITE_CHAR_UUID_ALT = "0000ff02-0000-1000-8000-00805f9b34fb";

  var CHUNK_SIZE = 182;
  var CHUNK_DELAY_MS = 40;
  var CONNECT_TIMEOUT_MS = 12000;
  var PRINT_TIMEOUT_MS = 20000;
  var POWER_CYCLE_HINT =
    "If this keeps happening, turn the printer off and back on, then try again.";

  function base64ToBytes(base64) {
    var binary = window.atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  /** Races `promise` against a `ms`-millisecond timer so a step that
   * never resolves (the hang this file's module docstring describes)
   * surfaces as a clear, timed-out error instead of leaving the
   * cashier staring at a frozen status line indefinitely. The original
   * promise is left to settle on its own after the timeout fires --
   * there's no way to cancel an in-flight Web Bluetooth call -- so
   * this only changes what the UI reports, not the underlying
   * request. */
  function withTimeout(promise, ms, timeoutMessage) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        reject(new Error(timeoutMessage));
      }, ms);
      promise.then(
        function (value) {
          clearTimeout(timer);
          resolve(value);
        },
        function (err) {
          clearTimeout(timer);
          reject(err);
        }
      );
    });
  }

  function findWriteCharacteristic(server) {
    var attempts = [
      [BLE_SERVICE_UUID, BLE_WRITE_CHAR_UUID],
      [BLE_SERVICE_UUID_ALT, BLE_WRITE_CHAR_UUID_ALT],
    ];

    function tryNext(index, lastError) {
      if (index >= attempts.length) {
        return Promise.reject(lastError || new Error("No matching printer service found."));
      }
      var serviceUuid = attempts[index][0];
      var charUuid = attempts[index][1];
      return server
        .getPrimaryService(serviceUuid)
        .then(function (service) {
          return service.getCharacteristic(charUuid);
        })
        .catch(function (err) {
          return tryNext(index + 1, err);
        });
    }

    return tryNext(0, null);
  }

  function writeInChunks(characteristic, bytes) {
    var offset = 0;

    function writeNext() {
      if (offset >= bytes.length) {
        return Promise.resolve();
      }
      var chunk = bytes.slice(offset, offset + CHUNK_SIZE);
      offset += CHUNK_SIZE;
      return characteristic
        .writeValueWithoutResponse(chunk)
        .then(function () {
          return sleep(CHUNK_DELAY_MS);
        })
        .then(writeNext);
    }

    return writeNext();
  }

  /**
   * Fetches the transaction's ESC/POS bytes from `payloadUrl` and
   * prints them directly over Web Bluetooth. `statusEl`, if given,
   * receives short plain-text status updates (connecting, printing,
   * done, or the error) so the cashier sees what's happening instead of
   * a silently-failing button.
   */
  function printEscposDirect(payloadUrl, statusEl) {
    function setStatus(text) {
      if (statusEl) statusEl.textContent = text;
    }

    if (!navigator.bluetooth) {
      setStatus(
        "Direct printing needs Chrome or Edge on a computer -- this browser doesn't " +
          "support Web Bluetooth. Use the RawBT option instead."
      );
      return Promise.resolve();
    }

    var chosenDevice = null;

    setStatus("Fetching receipt data...");
    return fetch(payloadUrl, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Could not load the receipt data (" + response.status + ").");
        }
        return response.json();
      })
      .then(function (data) {
        var bytes = base64ToBytes(data.escpos_base64);

        setStatus("Select your printer from the browser's Bluetooth picker...");
        // acceptAllDevices (not a services filter) on purpose: a
        // `filters: [{ services: [...] }]` requestDevice call only
        // matches a device that advertises that service UUID in its
        // Bluetooth broadcast packet, not one that merely has it once
        // connected. Found 2026-11-08 against the real PT-210: the
        // browser's picker reported "No compatible devices found" even
        // scanning right next to it -- the cheap UART-to-BLE module
        // these printers use broadcasts only a generic name, keeping
        // its actual GATT services hidden until after connection, so a
        // services filter can never see it. optionalServices still
        // has to list both UUID sets here, or the browser blocks
        // access to those services after connecting even once the
        // device itself is chosen.
        return navigator.bluetooth
          .requestDevice({
            acceptAllDevices: true,
            optionalServices: [BLE_SERVICE_UUID, BLE_SERVICE_UUID_ALT],
          })
          .then(function (device) {
            chosenDevice = device;
            // Defensive cleanup before reconnecting (see this file's
            // module docstring for the hang this guards against): the
            // browser hands back the same BluetoothDevice once
            // permission is granted, so if a previous print's
            // connection is still considered open from our side,
            // force it closed before asking for a new one.
            if (chosenDevice.gatt && chosenDevice.gatt.connected) {
              chosenDevice.gatt.disconnect();
            }
            setStatus("Connecting...");
            return withTimeout(
              chosenDevice.gatt.connect(),
              CONNECT_TIMEOUT_MS,
              "Connecting timed out. " + POWER_CYCLE_HINT
            );
          })
          .then(function (server) {
            return findWriteCharacteristic(server).then(function (characteristic) {
              setStatus("Printing...");
              return withTimeout(
                writeInChunks(characteristic, bytes),
                PRINT_TIMEOUT_MS,
                "Printing timed out partway through. " + POWER_CYCLE_HINT
              );
            });
          })
          .then(function () {
            if (chosenDevice.gatt && chosenDevice.gatt.connected) {
              chosenDevice.gatt.disconnect();
            }
            setStatus("Sent to printer.");
          });
      })
      .catch(function (err) {
        if (chosenDevice && chosenDevice.gatt && chosenDevice.gatt.connected) {
          chosenDevice.gatt.disconnect();
        }
        setStatus(
          "Could not print directly: " +
            (err && err.message ? err.message : err) +
            ". Try the RawBT option instead, or turn the printer off and on and try again."
        );
      });
  }

  window.SanServeAllEscposPrint = { printEscposDirect: printEscposDirect };
})(window);
