# Food photography

The menu and homepage use real food photographs from Wikimedia Commons. These are representative stock images, not photographs taken at N.K. Food Court. No generated food imagery is used. Closely related recipes may share a photo; the public credits page discloses every dish mapping.

The delivered catalog covers **185 menu dishes with 145 used photographs**, totaling approximately **14.85 MiB**. Each used asset was decoded and checked against its recorded checksum and attribution metadata. Desktop and mobile browser previews verified visible photos load without horizontal overflow.

## Runtime

- Original filenames, creator credits, source pages, license names/URLs and changes are recorded in `menu/data/photos/drinks.json`, `mains.json` and `snacks.json`.
- Optimized WebP assets live in `static/images/food/` and ship with the project. Browsing the menu needs no connection to Wikimedia or an image API.
- `menu/photos.py` loads the curated catalog; `MenuItem.photo_url` prefers an uploaded restaurant image, then the mapped stock photo.
- Every photo is credited at `/menu/photography/`, linked from the footer and menu. Resizing, format conversion and display crops are acknowledged. Individual images retain their respective licenses, including share-alike terms where applicable.
- An unmapped new dish uses a text placeholder rather than an unrelated food photo. Add its own image through Django Admin, or curate a new mapping and restart the app after editing the catalog.

## Updating assets

`scripts/food_photos.py` searches Commons and downloads selected files. It allows CC BY, CC BY-SA, CC0 and public-domain photos with creator information. Confirm the image content, provenance and license before choosing a file; filenames alone are not evidence that a photograph shows the intended dish.

```powershell
.\.venv\Scripts\python.exe scripts\food_photos.py search 'shahi paneer'
.\.venv\Scripts\python.exe scripts\food_photos.py sheet mains
```

The helper validates HTTPS, preserves aspect ratio, limits download size and converts images to WebP. Normal running and deployment use the already downloaded files. Do not remove original author/license metadata or relabel a stock photo as a photograph of the restaurant's own food.
