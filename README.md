# N.K. Food Court — Neelkanth Refreshments

A Django application for restaurant ordering, group dining, precise bill splitting and manually verified UPI payments. The backend uses Django authentication, Forms, the ORM and server-rendered templates. SQLite is the default database; PostgreSQL can be configured through environment variables.

**Live application:** [N.K. Food Court on PythonAnywhere](https://aishwary123321.pythonanywhere.com/) · **Source:** [Aishwary121/NK-food](https://github.com/Aishwary121/NK-food).

The hosted academic demonstration uses PythonAnywhere's Python 3.11 WSGI service, HTTPS, persistent SQLite/media directories and protected collector QR delivery. Its report fixtures use unique credentials and the fictional receiver `report-demo@example`, labelled **Report Demo - Do Not Pay**. These records demonstrate the workflow and do not represent bank transfers. Public development passwords below do not apply to the hosted accounts. The free host requires monthly renewal from its Web dashboard; the initial renewal date is 12 December 2026.

## Run locally on Windows

Install Python 3.11 or later from [python.org](https://www.python.org/downloads/windows/), including the Python launcher. Open PowerShell in this project directory.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_data
.\.venv\Scripts\python.exe manage.py runserver
```

Open **http://127.0.0.1:8000/**. The admin interface is at **http://127.0.0.1:8000/admin/**. Staff can also use the restaurant dashboard at **http://127.0.0.1:8000/dashboard/**.

Using the virtual environment's Python directly avoids PowerShell activation-policy problems. If preferred, activate with `.\.venv\Scripts\Activate.ps1` and then use `python` for the commands above. On macOS/Linux the equivalent interpreter path is `.venv/bin/python`.

Migrations are committed to the project. `makemigrations` is needed only after changing models:

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations
.\.venv\Scripts\python.exe manage.py migrate
```

To create an administrator with your own credentials:

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

## Demo accounts

`seed_data` adds the supplied menu and local demonstration accounts. It can be rerun without duplicating the supplied records. Existing menu edits, demo groups and account passwords are preserved. Default passwords are for local demonstration only; change them before any deployment. Use `seed_data --menu-only` to load only the menu; this flag is required when `DJANGO_DEBUG=false`.

| Role | Username | Password |
| --- | --- | --- |
| Administrator | `admin` | `Admin@12345` |
| Customer | `rahul` | `Rahul@123` |
| Customer | `aman` | `Aman@123` |
| Customer | `rohit` | `Rohit@123` |
| Customer | `karan` | `Karan@123` |

Use separate browser profiles or private windows to demonstrate several members at once. No UPI ID is seeded. Enter the collector's real UPI ID before attempting a payment.

Two sample groups are included:

- **Lunch at N.K. — sample bill** is finalized with two pairs, a ₹437.00 order and collector Rahul. Rahul owes **₹175.50**, Aman **₹107.50**, Rohit **₹73.50**, and Karan **₹80.50**. The kitchen order contains 14 Tawa Rotis, one Shahi Paneer, one Cold Coffee, one Dal Makhani and two Nimbu Paanis.
- **Our next lunch — open table** contains the same four members and an empty draft order, ready for testing the cart and sharing controls.

## Try the complete workflow

1. Register users, or sign in with a demo customer. Create a dining group and share its group code or join QR.
2. Sign in as the other members and join the group. In new equal-split groups, the host is the payment collector.
3. Select a group on the menu and add dishes to your personal cart. Adjust quantities, then add the selection to the table's order.
4. Review quantities on the order page. The full bill is divided equally across all members, including the host. Historical finalized itemized orders retain their original allocations.
5. Review the combined kitchen quantities and every member's bill. Only the organizer can finalize, using **Finalize & enable payments** at the top of the page to reach the finalization form. Finalization stores the payable amounts and locks ordinary editing.
6. Open the collector dashboard, then **Payment QR Management**. Enter the UPI ID and payee display name, upload a static QR if desired, and select the preferred method.
7. Members see **Pay ₹amount via UPI** at the top of the group and order pages once the bill is finalized and UPI is configured. The button opens a compatible mobile UPI app with the finalized amount and collector's UPI ID filled in. **View QR / payment status** provides the desktop QR, download and payment claim form. Waiting pages check for finalization or payment setup every 15 seconds while visible; the manual refresh link also works without JavaScript.
8. After actually paying in the UPI app, submit **I Have Paid** with the requested reference information. The payment becomes **Pending verification**.
9. The collector or an authorized administrator checks the incoming transfer independently and verifies or rejects it. The dashboard totals update from verified records only.
10. Open the order receipt and print it with the print action or your browser's print command.

## Payment design

The integrated feature requirements and acceptance criteria are documented in [Payment requirements](docs/PAYMENT_REQUIREMENTS.md).

There are two collector options. The preferred option controls the default member QR; configuring a static image does not remove the collector's dynamic UPI option when a valid UPI ID is present. Select **Payment collection not configured** to remove the only active method. Payment settings lock once any claim is pending or verified so the recipient cannot change during collection.

**Dynamic UPI QR.** Python's `qrcode` library generates PNG images on the server. Both the image and mobile link use the same encoded URI:

```text
upi://pay?pa=COLLECTOR_UPI_ID&pn=COLLECTOR_NAME&am=MEMBER_AMOUNT&cu=INR&tn=NK_FOODCOURT_ORDER_ID
```

Values are URL-encoded. The URI also includes a `tr` reference unique to the order and member, so members with equal shares still receive distinct payloads. The amount is formatted to two decimal places and read from `orders.BillAllocation`; it is never taken from browser query parameters or form fields. Members owing ₹175.50 and ₹107.50 therefore receive different amount-specific QR payloads. UPI address syntax is validated; this does not establish that an address exists or prove account ownership. The payer should check the recipient shown by their UPI app.

**Uploaded static QR.** Collector images use Django media storage in the private media directory. PNG, JPEG and WebP uploads may be at most 5 MB, 64–4096 pixels per side and 16 megapixels in total. The extension must match the decoded format. Animated images are rejected. Pillow verifies the image and re-encodes it as PNG to strip metadata and appended content; the normalized result must also fit the size limit. The image is delivered through an authenticated, authorized view. An arbitrary static image is not decoded or asserted to contain the member's amount. The payment screen explicitly tells members that they may need to enter the exact amount manually and to check the recipient in their UPI app. Upload validation checks image safety; it cannot prove that an image contains a valid UPI QR.

**Manual verification.** The state machine is:

```text
UNPAID → PENDING VERIFICATION → VERIFIED
                            ↘ REJECTED → PENDING VERIFICATION
```

Generating, viewing, downloading or scanning a QR, opening a UPI app, and submitting “I Have Paid” never mark payment verified. Only an authorized collector or administrator can review pending submissions. Payment transitions are audited. A verified payment cannot be verified a second time through the review workflow.

The app stores no UPI PINs, banking passwords, cards or bank-login credentials. It has no payment gateway, bank webhooks, transaction-status API, settlement reconciliation or automatic refund integration. Actual transfer success must be checked by the collector. UPI app handling depends on the installed app, device and browser; a desktop browser can display the QR but normally cannot launch a mobile UPI app.

The UPI field meanings are described in [Google Pay for India's payment request documentation](https://developers.google.com/pay/india/api/web/create-payment-method). This project uses a generic UPI URI, not Google's JavaScript Payment Request API.

## Bill splitting

New groups and existing draft orders divide the **entire final bill equally across every member, including the host**. A ₹300 bill for two members is ₹150 each, irrespective of who added the food. Joins and departures update draft shares; finalization locks the amounts used for payment.

All money uses `Decimal` and database `DecimalField`s. The grand total (food + tax − discount) is split once in integer paise. Remainder paise go deterministically by membership ID, so ₹100 across three members is ₹33.34, ₹33.33 and ₹33.33; no shares differ by more than one paisa. Individual item rounding cannot accumulate into an unequal final bill. Only quantities can be changed in equal-split orders; the browser cannot exclude a member or choose a different split mode.

The **host pays the full restaurant bill at the counter** and records that declaration separately. Other members pay their exact shares to the host's configured UPI account. The host sees each member's unpaid, pending verification, completed or rejected status, plus collection totals that exclude the host's own share. The host never transfers money to themselves. Recording a counter payment does not verify a bank transfer or complete members' claims. The group can be completed after the counter declaration and verification of every positive member reimbursement.

Previously finalized bills and the original ₹437 report fixture retain their item-based allocations and original collector. The service retains quantity/custom/percentage splitting only for this historical mode. New customer groups always use equal sharing. Migration `orders.0002_equal_split_policy` upgrades drafts, makes the host their collector and clears any previous collector's receiving settings when ownership changes. It never recalculates finalized amounts or marks a payment paid.

Item names and unit prices are snapshotted into the order, preserving old receipts when the menu changes. Finalization persists the member allocations used by payments. The consolidated kitchen view combines dishes. Allocation changes, counter declarations and payment reviews are audited.

Tax and discount are explicit order amounts; no jurisdiction-specific tax rate is assumed. Review their values before finalization. Changing a finalized order through ordinary customer forms is blocked.

For the existing PythonAnywhere SQLite deployment, `~/.virtualenvs/nk-food/bin/python deploy/upgrade_equal_split.py ~/.nk-food-environment.json` creates a private database backup, migrates, verifies unchanged historical allocations and equal draft totals, collects static assets and runs deployment checks. Reload the web app after the command passes.

## Menu and images

The seed fixture now matches all 185 dishes across 19 categories against the two supplied menu photographs. The photo audit corrected 12 prices, 12 dish names and one category name, and added both thali descriptions. Ice Cream remains MRP with no invented numeric price; an administrator must set its price before it can be ordered. Time-limited dishes have availability windows, using the `Asia/Kolkata` timezone. Availability and prices can be managed in Django Admin.

The two menu photographs supplied from Downloads were checked item by item. See the [photo audit and complete correction list](docs/MENU_PHOTO_AUDIT.md). To apply the authoritative photo catalog to an already seeded database, run:

```powershell
.\.venv\Scripts\python.exe manage.py seed_data --menu-only --sync-menu
```

This explicit sync preserves item IDs and historical order/payment records, resolves legacy names without creating duplicates, and updates the catalog's names, prices, printed descriptions and time restrictions. It preserves uploaded images and enabled flags. Ordinary seeding still preserves administrator edits. Conflicting legacy and canonical records are reported instead of silently merged.

The printed opening hours and “Since 2014” appear on the home and menu pages. These hours are informational; university holidays are not inferred and the restaurant-wide schedule does not automatically block ordering. Individual dish availability windows are enforced.

The menu now uses real, freely licensed food photographs stored locally in `static/images/food/`, with dish mappings and original creator/license details in `menu/data/photos/`. Photos are selected to match the dish or a closely related recipe variant; they do not claim to show N.K. Food Court's exact serving. A short note on the menu explains that presentation may vary. The [Photo credits page](http://127.0.0.1:8000/menu/photography/) lists every used photograph, its creator, source and license. No AI-generated food images are used.

Images are optimized as WebP and loaded lazily on menu cards. An administrator's uploaded dish photo takes precedence over the included stock photograph. Unknown new dishes show a text placeholder until a photo is uploaded. There are no request-time external image searches or hotlinked image dependencies. The homepage also uses a real vegetarian thali photograph. Fonts have system fallbacks, so the core interface remains usable without access to Google Fonts.

`scripts/food_photos.py` is an optional authoring helper for searching Wikimedia Commons, downloading selected freely licensed files and preparing visual contact sheets. It is not required to run the site. Preserve the attribution records and linked licenses when redistributing the photographs. See [food photography notes](docs/FOOD_PHOTOGRAPHY.md).

## Project map

| Location | Responsibility |
| --- | --- |
| `nk_foodcourt/` | Settings, root URLs, WSGI and ASGI entry points |
| `accounts/` | Registration, Django authentication, profiles and password changes |
| `menu/` | Categories, dishes, availability, search, admin and seed data |
| `groups/` | Group codes, join QR, memberships, pairs and collector assignment |
| `orders/` | Personal carts, consolidated orders, allocation services and receipts |
| `payments/` | Collector settings, private QR storage, UPI payloads, payment review and audit |
| `dashboard/` | Staff-only operational summaries |
| `templates/` | Django template inheritance and server-rendered forms |
| `static/` | Responsive CSS, small progressive enhancements and optimized local food photos |
| `media/public/` | Uploaded menu images |
| `media/private/` | Private collector QR images; never expose as a public directory |

The UI remains functional for essential form submissions without JavaScript. JavaScript adds navigation, print actions, confirmation dialogs and loading indicators; it does not calculate payment amounts or verify transfers.

## Checks and tests

Install development test dependencies first. `zxing-cpp` decodes the generated PNGs so the QR tests check the actual image payload, including its receiver and member-specific amount.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

The automated tests cover authentication and access controls, menu behavior, group membership and pairs, exact allocation arithmetic, order finalization, dynamic UPI payloads and QR image decoding, private image validation, and payment submission/review. Tests create a separate database. Real UPI transfers and phone scanning require a manual device check; the automated suite does not transfer money. The development server serves the local app; browser display and physical phone scanning are separate manual checks.

For a focused payment run:

```powershell
.\.venv\Scripts\python.exe manage.py test payments
```

## Configuration and deployment

See [deployment instructions](docs/DEPLOYMENT.md) for the PythonAnywhere setup helper, durable data paths, HTTPS configuration and Linux deployment alternatives. `deploy/prepare_report_demo.py` is a separate, explicit report-fixture helper; normal deployment only loads menu data. The report helper refuses to alter existing demonstration usernames and never prints its generated login passwords.

`.env.example` documents the supported environment variables. **Django does not automatically read a `.env` file in this project.** Set these in PowerShell or your hosting platform's environment. Local development needs no credentials.

```powershell
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost'
```

For production, provide `DJANGO_SECRET_KEY` from a secret manager, set `DJANGO_DEBUG=false`, and set `DJANGO_ALLOWED_HOSTS` to the actual hostnames. Production settings require a secret and enable HTTPS-related protections. Review HTTPS termination and proxy configuration for your hosting environment before starting the application. Do not use the development server for production. Follow the [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), run `manage.py check --deploy` with production settings, and serve collected static assets through your web server or storage service.

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Keep `media/private/` private. Only `media/public/` is eligible for public image serving. Apply request-size limits at the reverse proxy, back up the database and uploaded files together, and restrict infrastructure access. Application audit records are protected by the application workflow, not a substitute for tamper-resistant infrastructure logging.

To use PostgreSQL, install its driver and set `DB_ENGINE=postgresql`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` and optionally `DB_PORT`:

```powershell
.\.venv\Scripts\python.exe -m pip install 'psycopg[binary]'
```

Run migrations against the chosen database. Switching the configuration does not transfer existing SQLite data. SQLite is suitable for the local demonstration; use PostgreSQL and exercise concurrency behavior before a busy multi-user deployment.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `python` not found | Install Python and reopen the terminal, or use `py -3`. |
| Activation is blocked | Use `.\.venv\Scripts\python.exe` directly. |
| `No module named django` | Install requirements using the same virtual-environment interpreter used to run the server. |
| QR tests report missing `zxingcpp` | Install `requirements-dev.txt` with the virtual-environment interpreter before running the test suite. |
| `no such table` | Run `manage.py migrate`. |
| Menu is empty | Run `manage.py seed_data` in local development. |
| A dish is unavailable | Check its price, enabled flag and time window in Admin. |
| `DisallowedHost` | Add the exact hostname to `DJANGO_ALLOWED_HOSTS`, then restart the server. |
| Images are missing in production | Configure public media and collected static-file serving; keep private QR media protected. |
| “Open UPI App” does nothing | Try a compatible mobile browser with a UPI app installed, or scan the desktop QR with a phone. |
| Static QR shows a different amount | Enter the displayed member bill manually if the UPI app allows it; otherwise ask the collector for a dynamic QR. |
| Payment still says pending | The collector must check the incoming transfer and explicitly verify it. |
| A member cannot open another member's payment | This is intentional access control. Use the collector dashboard for authorized review. |

## Django syllabus mapping

The project demonstrates apps and management commands (Unit I), URL routing and request/response views (II), DTL inheritance and loops (III), validated Forms with GET/POST and CSRF (IV), ORM models/migrations/admin/relationships (V), and sessions with authentication and protected views (VI). Financial calculations live in testable services rather than templates or JavaScript.
