# SanServeAll — Standalone Note: Bluetooth Receipt Printer Integration

**Status:** Open hardware/architecture decision, not yet folded into Phases 2–3. Raised outside the original manuscript, which only shows a software "Print" button on the Transaction Receipt screen (Fig. 3-17) without specifying the printing hardware or connection method.

**Context:** Counter device confirmed as an **Android tablet**. Target hardware: a **Bluetooth thermal receipt printer**.

---

## The Core Constraint

Most low-cost Bluetooth thermal receipt printers (58mm/80mm ESC/POS style) use **Bluetooth Classic SPP** (Serial Port Profile), not BLE. Chrome's **Web Bluetooth API** — the only Bluetooth access a webpage has at all — only supports **BLE/GATT** devices. This means, for most printers in this category, **a plain browser tab cannot talk to the printer directly**, regardless of how the JS is written. This is a browser-platform limitation, not something specific to SanServeAll's code.

A minority of newer printer models do support BLE printing directly — if a specific printer is already owned, check its spec sheet for documented BLE/GATT printing support before ruling this out.

---

## Options, in Order of Effort

### 1. RawBT (Android print-bridge app) — recommended first attempt
- Free Android app that pairs with the printer over Bluetooth (handles both SPP and BLE under the hood) and exposes an Intent/URL scheme a webpage can invoke to send a raw ESC/POS print job.
- No native development, no app-store submission — `pos.js` builds the ESC/POS payload, fires the intent RawBT listens for.
- Most common real-world solution for "web app + Android tablet + cheap Bluetooth printer."

### 2. Thin custom Android WebView wrapper — fallback if RawBT is too limited
- A minimal Android app that loads SanServeAll in a WebView, with a small native Bluetooth SPP bridge exposed to page JS (e.g. `window.AndroidPrinter.printReceipt(...)`).
- **Scope note:** Phase 1 explicitly excluded native mobile apps from this project. A print bridge like this would be a deliberate, narrow, documented exception (hardware bridge only, not a general mobile client) — should be raised with the adviser rather than treated as within the original defended scope.

### 3. Network/Wi-Fi printing — worth considering if hardware isn't purchased yet
- Some equivalent thermal printers (e.g., Epson TM-series) support Wi-Fi printing via a documented HTTP-based protocol (Epson ePOS-Print XML API).
- If reachable over the café's local network instead of Bluetooth, the web app's JS can send the print job as a plain HTTP request — no native bridge, no app-store component, cleanest fit for the pure-web architecture from Phase 2.
- Recommend evaluating this before finalizing printer hardware purchases, since it sidesteps the whole Bluetooth/Web Bluetooth limitation.

---

## Where This Would Live in the Project Structure (Phase 4)

- ESC/POS payload-building logic → `apps/pos/services.py` or a new `apps/pos/printing.py`
- Client-side print trigger → `static/js/pos_print.js`
- If Option 2 (WebView wrapper) is chosen → a new top-level folder, e.g. `mobile-bridge/`, clearly separated from and documented as distinct from the "no native mobile app" scope decision

---

## Decision Needed Before This Can Be Finalized

- [ ] Has printer hardware already been purchased? If yes — which exact model (needed to confirm Bluetooth SPP vs. BLE vs. Wi-Fi support)?
- [ ] If not yet purchased — is Wi-Fi/network printing (Option 3) acceptable, avoiding the Bluetooth problem entirely?
- [ ] If Bluetooth is required — is RawBT (Option 1) acceptable, or does the adviser/panel expect a fully custom native solution (Option 2)?

Once these are answered, this note should be merged into Phase 2 (Architecture) and Phase 3 (Tech Stack) as a proper addendum.
