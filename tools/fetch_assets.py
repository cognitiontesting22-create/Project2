"""Download the demo photographs and self-hosted typefaces once, at setup time."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent / "static"
PHOTOS = {
    "hero": "photo-1532550907401-a500c9a57435",
    "chicken": "photo-1532550907401-a500c9a57435",
    "salad": "photo-1512621776951-a57141f2eefd",
    "pasta": "photo-1473093295043-cdd812d0e601",
    "salmon": "photo-1467003909585-2f8a72700288",
    "toast": "photo-1525351484163-7529414344d8",
    "pancakes": "photo-1528207776546-365bb710ee93",
    "risotto": "photo-1476124369491-e7addf5db371",
    "curry": "photo-1603894584373-5ac82b2ae398",
    "bread": "photo-1509440159596-0249088772ff",
    "tacos": "photo-1551504734-5ee1c4a1479b",
    "shakshuka": "photo-1590412200988-a436970781fa",
    "cookies": "photo-1499636136210-6f4ee915583e",
}
PHOTO_URLS = {"bread": "https://images.pexels.com/photos/5419302/pexels-photo-5419302.jpeg?auto=compress&cs=tinysrgb&w=900"}

def download_photo(item, refresh=False):
    name, identifier = item
    destination = ROOT / "images" / f"{name}.jpg"
    if destination.exists() and not refresh:
        return f"{name}: already downloaded"
    width = 1100 if name == "hero" else 900
    url = PHOTO_URLS.get(name, f"https://images.unsplash.com/{identifier}?auto=format&fit=crop&w={width}&q=85")
    request = Request(url, headers={"User-Agent": "Savor-development/1.0"})
    with urlopen(request, timeout=60) as response:
        destination.write_bytes(response.read())
    return f"{name}: saved"

def download_font(family, name, refresh=False):
    destination = ROOT / "fonts" / f"{name}.ttf"
    if destination.exists() and not refresh:
        return
    filename = {"Inter": "Inter[opsz,wght].ttf", "Fraunces": "Fraunces[SOFT,WONK,opsz,wght].ttf"}[family]
    url = f"https://raw.githubusercontent.com/google/fonts/main/ofl/{name}/{quote(filename)}"
    with urlopen(url, timeout=30) as response:
        destination.write_bytes(response.read())
    print(f"{name}: saved")

def download_license(name):
    destination = ROOT / "fonts" / f"{name}-OFL.txt"
    if not destination.exists():
        with urlopen(f"https://raw.githubusercontent.com/google/fonts/main/ofl/{name}/OFL.txt", timeout=30) as response:
            destination.write_bytes(response.read())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-fonts", action="store_true")
    parser.add_argument("--refresh-photo", choices=PHOTOS)
    arguments = parser.parse_args()
    (ROOT / "images").mkdir(parents=True, exist_ok=True)
    (ROOT / "fonts").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as executor:
        for result in executor.map(lambda item: download_photo(item, refresh=item[0] == arguments.refresh_photo), PHOTOS.items()):
            print(result)
    download_font("Inter", "inter", refresh=arguments.refresh_fonts)
    download_font("Fraunces", "fraunces", refresh=arguments.refresh_fonts)
    download_license("inter")
    download_license("fraunces")
