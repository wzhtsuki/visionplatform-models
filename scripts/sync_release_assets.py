import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path

import requests


API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "VisionPlatform-Mirror-Sync"


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def github_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
    )
    return session


def api_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    expected: tuple[int, ...] = (200,),
    **kwargs,
) -> dict | list:
    response = session.request(method, url, timeout=(30, 300), **kwargs)
    if response.status_code not in expected:
        raise RuntimeError(
            f"{method} {url} failed with {response.status_code}: {response.text[:400]}"
        )
    if not response.content:
        return {}
    return response.json()


def ensure_release(session: requests.Session, repo: str, tag: str, name: str) -> dict:
    url = f"{API_BASE}/repos/{repo}/releases/tags/{tag}"
    response = session.get(url, timeout=(30, 300))
    if response.status_code == 200:
        return response.json()
    if response.status_code != 404:
        raise RuntimeError(
            f"GET {url} failed with {response.status_code}: {response.text[:400]}"
        )
    return api_json(
        session,
        "POST",
        f"{API_BASE}/repos/{repo}/releases",
        expected=(201,),
        json={
            "tag_name": tag,
            "name": name,
            "draft": False,
            "prerelease": False,
            "generate_release_notes": False,
            "make_latest": "true",
        },
    )


def list_release_assets(
    session: requests.Session, repo: str, release_id: int
) -> dict[str, dict]:
    page = 1
    assets: dict[str, dict] = {}
    while True:
        page_assets = api_json(
            session,
            "GET",
            f"{API_BASE}/repos/{repo}/releases/{release_id}/assets",
            params={"per_page": 100, "page": page},
        )
        if not page_assets:
            break
        for asset in page_assets:
            assets[asset["name"]] = asset
        if len(page_assets) < 100:
            break
        page += 1
    return assets


def download_file(
    session: requests.Session, source_url: str, output_path: Path, retries: int = 3
) -> int:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with session.get(source_url, stream=True, timeout=(30, 1800)) as response:
                response.raise_for_status()
                with output_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if chunk:
                            file.write(chunk)
            return output_path.stat().st_size
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if output_path.exists():
                output_path.unlink()
            if attempt < retries:
                time.sleep(3 * attempt)
    assert last_error is not None
    raise last_error


def upload_asset(
    session: requests.Session, upload_url: str, file_path: Path, filename: str
) -> dict:
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with file_path.open("rb") as file:
        response = session.post(
            upload_url,
            params={"name": filename},
            headers={
                "Accept": "application/vnd.github+json",
                "Content-Type": content_type,
                "User-Agent": USER_AGENT,
            },
            data=file,
            timeout=(30, 3600),
        )
    if response.status_code not in (201,):
        raise RuntimeError(
            f"Upload failed for {filename}: {response.status_code} {response.text[:400]}"
        )
    return response.json()


def append_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as file:
        for line in lines:
            file.write(f"{line}\n")


def main() -> int:
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    manifest_path = Path(os.environ.get("MANIFEST_PATH", "mirror_manifest.json"))
    release_tag = os.environ.get("RELEASE_TAG", "v1")
    release_name = os.environ.get("RELEASE_NAME", "Auto-labeling model mirror")

    if not repo:
        raise RuntimeError("REPO or GITHUB_REPOSITORY is required")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")

    manifest = load_manifest(manifest_path)
    entries = manifest["entries"]
    start_index = int(os.environ.get("START_INDEX", "0"))
    end_index = int(os.environ.get("END_INDEX", str(len(entries))))
    subset = entries[start_index:end_index]

    session = github_session(token)
    release = ensure_release(session, repo, release_tag, release_name)
    existing_assets = list_release_assets(session, repo, release["id"])
    upload_url = release["upload_url"].split("{", 1)[0]

    downloaded = 0
    uploaded = 0
    skipped = 0
    failures: list[dict[str, str]] = []

    temp_root = Path(tempfile.gettempdir()) / "visionplatform-model-mirror"
    temp_root.mkdir(parents=True, exist_ok=True)

    print(
        f"Processing entries {start_index}..{end_index - 1} "
        f"for {repo} release {release_tag}"
    )

    for absolute_index, entry in enumerate(subset, start=start_index):
        filename = entry["filename"]
        source_url = entry["source_url"]

        if filename in existing_assets:
            print(f"[SKIP] {absolute_index}: {filename} already exists")
            skipped += 1
            continue

        temp_file = temp_root / filename
        try:
            print(f"[DOWNLOAD] {absolute_index}: {filename}")
            size = download_file(session, source_url, temp_file)
            downloaded += size

            print(f"[UPLOAD] {absolute_index}: {filename} ({size} bytes)")
            upload_asset(session, upload_url, temp_file, filename)
            uploaded += 1
            print(f"[OK] {absolute_index}: {filename}")
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            print(f"[FAIL] {absolute_index}: {filename}: {message}")
            failures.append(
                {
                    "filename": filename,
                    "source_url": source_url,
                    "error": message,
                }
            )
        finally:
            if temp_file.exists():
                temp_file.unlink()

    append_summary(
        [
            f"## Chunk {start_index}-{end_index}",
            f"- Uploaded files: {uploaded}",
            f"- Skipped existing files: {skipped}",
            f"- Downloaded bytes this chunk: {downloaded}",
            f"- Failures: {len(failures)}",
        ]
    )

    if failures:
        sample = failures[:10]
        append_summary(["", "### Failure sample", "```json", json.dumps(sample, ensure_ascii=False, indent=2), "```"])
        print(json.dumps({"failures": failures}, ensure_ascii=False, indent=2))

    print(
        json.dumps(
            {
                "start_index": start_index,
                "end_index": end_index,
                "uploaded": uploaded,
                "skipped": skipped,
                "downloaded_bytes": downloaded,
                "failures": len(failures),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
