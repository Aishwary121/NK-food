# Menu photograph audit

Checked the two supplied WhatsApp photographs dated 2026-10-29 against the original app menu. The photos are the source of truth for this correction. All **185 dishes** are present: **86 on the front and 99 on the back**, grouped into the app's **19 categories**. Typography and capitalization are normalized for readability; dish names, prices, portions, options and time notes follow the photos. The app's descriptive grouping “Thali & Combos” labels the otherwise untitled combo panel.

## Price corrections

| Dish | Previous app price | Printed price |
| --- | ---: | ---: |
| Spl. Sweet Lassi | 50 | 55 |
| Dahi Puri 5PC | 65 | 60 |
| Papri Chaat | 60 | 65 |
| Samosa (Till 5PM) | 65 | 15 |
| Bread Pakoda (Till 5PM) | 15 | 25 |
| Chana Samosa Chaat 2pc (Till 5PM) | 25 | 65 |
| Pav Bhaji | 65 | 80 |
| Spring Roll | 90 | 20 |
| Spinach and Corn Sandwich | 110 | 105 |
| Triple Protein Salad | 105 | 110 |
| Whole Wheat Tofu Bhurji Wrap | 110 | 100 |
| Paneer Tikka Rice Bowl | 100 | 155 |

All prices are INR. The photographed price column slopes below its corresponding dish labels; reading the rows by sequence is particularly important for Chaat Bhandar. Dahi Puri is 60 and Papri Chaat is 65. Spring Roll is printed as 20; no price was substituted based on what seemed more typical. All prices on the back photo already matched.

## Names and descriptions

- Nimboo Soda → Nimbu Soda.
- Black Currant Shake → Black current Shake, preserving the printed spelling.
- Tawa Parantha category and its three dishes → Tawa Prantha / Prantha.
- Kaccha Gobhi Matar → Keema Gobhi Matar.
- Fried Potato Chilly → Fried Rice Potato Chilly.
- Kadhai Paneer / Kadhai Veg → Kadahi Paneer / Kadahi Veg.
- Lachha Parantha, Mirchi Lachha Parantha and Lachha Parantha with Dal 2pc → the corresponding “Prantha” names.

Normal Thali includes **2pc Tawa Butter Chapati + Rice + Sabji + Dal + Raita + Salad + Sprouts + Pickle**.

N.K Special Thali includes **2pc Tandoori Butter Roti + Rice + Sabji + Dal + Paneer Sabji + Raita + Salad + Pickle + Dessert**.

The photographed portions/options remain in the dish names: 6pc Golgappe, 5pc Dahi Puri, 2pc Chana Samosa Chaat, 8-inch pizzas, dry/gravy variants and stuffed-prantha options. The existing till-5-PM restrictions, winter-special labels and Ice Cream's unpriced **MRP** designation are retained. No numeric Ice Cream price is inferred.

## Printed opening hours

| Days | Daytime | Evening |
| --- | --- | --- |
| Monday–Saturday | 8:00 AM–5:30 PM | 6:30 PM–10:30 PM |
| Sunday & university holidays | 11:00 AM–4:00 PM | 6:30 PM–10:30 PM |

These hours and “Neelkanth Refreshments · Since 2014” appear on the home/menu pages. They are informational: the app does not infer university holidays or automatically close ordering by this schedule. Individual dish availability windows remain enforced. A partial phone number at the edge of the photo is not transcribed because it is cropped.

## Applying the corrected catalog

```powershell
.\.venv\Scripts\python.exe manage.py seed_data --menu-only --sync-menu
```

The explicit sync updates menu records in place, using legacy name aliases to avoid duplicates. It preserves menu item IDs, images, enabled flags, order snapshots, finalized bills and payment history. Ordinary `seed_data` continues to preserve administrator edits. If both a legacy and canonical name already exist, sync stops atomically rather than deleting or merging records.

Sources: `WhatsApp Image 2026-10-29 at 8.47.59 PM.jpeg` (front) and `WhatsApp Image 2026-10-29 at 8.47.59 PM (1).jpeg` (back), supplied by the user. Decorative illustrations are not additional menu items.
