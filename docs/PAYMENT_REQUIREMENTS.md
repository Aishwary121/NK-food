# Payment requirements ? current testing demo

The current requested experience is a testing-only payment simulation. `PAYMENT_DEMO_MODE=true` is the default.

- A finalized member bill has a **Pay ?amount (demo)** button. One authenticated POST immediately records `DEMO_PAID`; no real transfer, app launch, QR, UPI configuration, reference or collector approval is needed.
- The exact amount is read from the finalized allocation. A member can simulate only their own positive share. Draft, cancelled and host self-transfer payments are rejected.
- Repeated clicks are idempotent and create one transition audit event. Existing bank-verified statuses are preserved.
- The host sees each member's **Demo paid** status and simulated total. Its open dashboard checks for changes automatically. Simulated totals remain separate from verified receipts.
- Counter payment simulation has its own `is_demo` flag. The group can be completed after the demo counter action and settled member shares.
- Demo records do not count as verified receipts or permit real-mode group completion. All demo screens say no money is transferred.
- Real payment setup, QR, claim and verification endpoints are disabled in demo mode. Previously stored records are retained.

The following specification documents the retained implementation used only when `PAYMENT_DEMO_MODE=false`.

## Retained UPI QR payment requirements

This specification supplements the N.K. Food Court ordering and bill-splitting requirements. The implementation lives in the Django `payments` app and uses finalized `orders.BillAllocation` records.

## Collector controls

- The designated collector or a staff administrator can configure each order's payment settings.
- Support both an uploaded static QR image and a UPI ID with account display name. Choose dynamic QR, uploaded QR, or unconfigured collection as the preferred method.
- Allow preview, replacement and removal of uploaded images. Dynamic previews use actual finalized member allocations.
- Changing the collector before finalization clears the previous receiver settings and records an audit event. Collector identity is fixed after finalization.
- Freeze receiver settings while any claim is pending or verified, preserving the destination under review.

## Dynamic QR and member interface

Generate each PNG on the server with Python `qrcode`. Its payload is a URL-encoded UPI URI:

```text
upi://pay?pa=COLLECTOR_UPI_ID&pn=COLLECTOR_NAME&am=MEMBER_AMOUNT&cu=INR&tn=NK_FOODCOURT_ORDER_ID&tr=MEMBER_REFERENCE
```

The standard fields follow the requested format. The additional `tr` reference distinguishes members even when their amounts are equal. `am` is the exact two-decimal finalized allocation, reloaded from the database. Browser amounts, receiver IDs, or mutated caller objects cannot replace these values.

The member screen shows their name, payable amount, collector, selected QR, instructions, download action, UPI deep link when a UPI ID exists, and an “I Have Paid” form. A static-only configuration cannot offer a reliable UPI deep link without an entered UPI ID. Desktop QR images display at 320 pixels and shrink to fit mobile layouts. Pending and verified screens avoid prompting another payment; zero allocations require no payment.

## Static upload handling

Accept PNG, JPEG and WebP images up to 5 MB, between 64 and 4096 pixels per side and no more than 16 megapixels. Validate decoded content, extension, dimensions and animation; re-encode pixels into a clean PNG without metadata or appended payloads. Validate the normalized output size too.

Store QR images under private Django media storage with random filenames. Serve images through authenticated member or collector views with private/no-store caching. Public media routes never expose the private storage directory. Delete replaced/removed images after the database transaction commits.

Image validation does not establish that a file contains a UPI QR, the intended receiver, or the correct amount. The interface always warns that a static QR may require manual amount entry and asks the member to check the receiver in the UPI app.

## Verification and audit

```text
UNPAID -> PENDING VERIFICATION -> VERIFIED
                              -> REJECTED -> PENDING VERIFICATION
```

Only the allocation's member can submit a claim. Only the assigned collector or staff administrator can verify or reject a pending claim. Rejection requires a reason. Duplicate submissions and duplicate reviews are rejected. Received totals count only verified allocations.

Generating, viewing, downloading or scanning a QR and opening a UPI app never confirm payment. There is no bank callback or automated verification. The collector independently checks receipt. Audit records capture actor, time, transition and review reason; the Django admin exposes financial records read-only. Infrastructure administrators remain responsible for database integrity and backups.

No UPI PIN or banking-credential field exists. UPI IDs receive syntax validation; this cannot prove account ownership. State-changing forms use CSRF protection and server-side authorization.

## Acceptance checks

`payments/tests.py` covers actual PNG decoding (with `requirements-dev.txt`), exact amounts, receiver ID, equal-amount member references, tampered browser parameters, safe and invalid image uploads, storage access, replacement/removal, claim/rejection/resubmission/verification, unauthorized reviews, duplicate reviews, disabled methods and accurate totals.

`menu/tests.py` exercises the full multi-user journey through HTTP forms. The seeded bill produces Rahul INR 175.50 and Aman INR 107.50 from menu prices and allocations. `groups/tests.py` and `orders/tests.py` cover collector reassignment, finalized locks, four-person splitting and exact total preservation.

Automated tests scan QR image bytes; real phone scanning, app launching and actual transfers must be checked on compatible devices with the collector's real UPI ID. Tests never transfer money.
