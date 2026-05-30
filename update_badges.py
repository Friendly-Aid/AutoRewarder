import json
import urllib.request
import os

REPO = "safarsin/AutoRewarder"
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN")


def format_number(num):
    if num >= 1000:
        return f"{num/1000:.1f}k"
    return str(num)


def fetch_api(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {GIST_TOKEN}")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def main():
    releases = fetch_api(f"https://api.github.com/repos/{REPO}/releases")
    total_downloads = sum(
        asset["download_count"]
        for release in releases
        for asset in release.get("assets", [])
    )

    repo_info = fetch_api(f"https://api.github.com/repos/{REPO}")
    stars = repo_info.get("stargazers_count", 0)

    downloads_badge = {
        "schemaVersion": 1,
        "label": "DOWNLOADS",
        "message": format_number(total_downloads),
        "color": "3FB950",
        "style": "for-the-badge",
    }

    stars_badge = {
        "schemaVersion": 1,
        "label": "STARS",
        "message": format_number(stars),
        "color": "e3b341",
        "style": "for-the-badge",
    }

    gist_url = f"https://api.github.com/gists/{GIST_ID}"
    gist_data = {
        "files": {
            "downloads.json": {"content": json.dumps(downloads_badge)},
            "stars.json": {"content": json.dumps(stars_badge)},
        }
    }

    patch_req = urllib.request.Request(
        gist_url, data=json.dumps(gist_data).encode("utf-8"), method="PATCH"
    )
    patch_req.add_header("Authorization", f"token {GIST_TOKEN}")
    patch_req.add_header("Accept", "application/vnd.github.v3+json")

    with urllib.request.urlopen(patch_req):
        print(f"Success! Downloads: {total_downloads}, Stars: {stars}")


if __name__ == "__main__":
    main()
