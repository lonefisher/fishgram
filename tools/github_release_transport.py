"""Narrow GitHub REST transport used only by the reviewed FishGram publisher.

The transport cannot select an arbitrary API host or repository. Authentication
is provided from environment-backed values by the caller; source-repository
writes require a distinct GitHub App installation token.
"""
from __future__ import annotations

import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PRODUCT_REPOSITORY = "lonefisher/fishgram"
SOURCE_REPOSITORY = "lonefisher/tdesktop"
API_ROOT = "https://api.github.com"
PAGES_ROOT = "https://lonefisher.github.io/fishgram/"
API_VERSION = "2022-11-28"


class TransportError(RuntimeError):
    """Safe-to-display transport refusal; deliberately excludes API bodies."""


class PublicAssetRedirects(HTTPRedirectHandler):
    """Enforce the host boundary before urllib follows any redirect."""

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        origin, target = urlsplit(request.full_url), urlsplit(new_url)
        authenticated = any(name.casefold() == 'authorization'
                            for name, _ in request.header_items())
        public_asset = (origin.scheme == 'https' and origin.hostname == 'github.com'
                        and origin.path.startswith('/lonefisher/fishgram/releases/download/'))
        allowed = (public_asset and not authenticated and target.scheme == 'https'
                   and target.hostname in {'release-assets.githubusercontent.com',
                                           'objects.githubusercontent.com'}
                   and target.port in (None, 443) and not target.username and not target.password)
        if not allowed:
            raise TransportError('GitHub redirect was rejected before forwarding the request.')
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def _default_request(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: int) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(PublicAssetRedirects()).open(request, timeout=timeout) as response:
            final = urlsplit(response.geturl())
            initial = urlsplit(url)
            public_asset_redirect = ("Authorization" not in headers and initial.hostname == "github.com"
                                     and final.hostname in {"release-assets.githubusercontent.com",
                                                            "objects.githubusercontent.com"})
            if (final.scheme != "https" or final.hostname != initial.hostname and not public_asset_redirect):
                raise TransportError("GitHub request redirected outside its approved HTTPS host.")
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except (URLError, TimeoutError, OSError) as error:
        raise TransportError("GitHub request failed; response details were suppressed.") from error


class GitHubReleaseTransport:
    """GitHub REST adapter with repository, host, and token-purpose boundaries."""

    def __init__(self, *, product_token: str, source_token: str,
                 request=None, timeout: int = 30):
        if (not isinstance(product_token, str) or not product_token.strip()
                or not isinstance(source_token, str) or not source_token.strip()):
            raise TransportError("Separate product and source GitHub tokens are required.")
        if product_token == source_token:
            raise TransportError("The source repository requires its distinct repository-scoped App token.")
        if not 1 <= timeout <= 120:
            raise TransportError("GitHub request timeout is outside the permitted range.")
        self._product_token = product_token
        self._source_token = source_token
        self._request = request or _default_request
        self._timeout = timeout
        self._pages: dict | None = None
        self._release_by_tag: dict[str, dict] = {}

    @classmethod
    def from_environment(cls, *, request=None):
        """Construct using environment values only; never accepts CLI token text."""
        parent = os.environ.get("PRODUCT_GITHUB_APP_TOKEN", "")
        source = os.environ.get("SOURCE_GITHUB_APP_TOKEN", "")
        if not parent or not source:
            raise TransportError("Separate PRODUCT_GITHUB_APP_TOKEN and SOURCE_GITHUB_APP_TOKEN are required.")
        return cls(product_token=parent, source_token=source, request=request)

    @staticmethod
    def _repository(repository: str) -> str:
        if repository not in {PRODUCT_REPOSITORY, SOURCE_REPOSITORY}:
            raise TransportError("Repository is not allowlisted for FishGram publication.")
        return repository

    def _api(self, method: str, path: str, *, repository: str,
             payload: dict | None = None, raw: bytes | None = None,
             upload: bool = False, source_write: bool = False,
             expected: tuple[int, ...] = (200,)) -> tuple[int, dict, bytes]:
        repository = self._repository(repository)
        if not path.startswith("/repos/" + repository + "/"):
            raise TransportError("API path does not match the approved repository.")
        url = ("https://uploads.github.com" if upload else API_ROOT) + path
        parsed = urlsplit(url)
        expected_host = "uploads.github.com" if upload else "api.github.com"
        if parsed.scheme != "https" or parsed.hostname != expected_host or parsed.port is not None:
            raise TransportError("GitHub API host is outside the approved HTTPS allowlist.")
        token = self._source_token if source_write or repository == SOURCE_REPOSITORY else self._product_token
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "FishGram-release-publisher",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif raw is not None:
            body = raw
            headers["Content-Type"] = "application/octet-stream"
        else:
            body = None
        try:
            status, response_headers, response_body = self._request(
                method, url, headers, body, self._timeout)
        except TransportError:
            raise
        except Exception as error:
            raise TransportError("GitHub request failed; response details were suppressed.") from error
        if status not in expected:
            raise TransportError(f"GitHub API returned HTTP {status} for the requested operation.")
        return status, response_headers, response_body

    @staticmethod
    def _json(data: bytes, label: str) -> dict:
        try:
            result = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransportError(label + " response was not valid JSON.") from error
        if not isinstance(result, dict):
            raise TransportError(label + " response was not a JSON object.")
        return result

    def tag_exists(self, repository: str, tag: str) -> bool:
        repository = self._repository(repository)
        if not tag or "/" in tag or ".." in tag:
            raise TransportError("Tag name is malformed.")
        path = f"/repos/{repository}/git/ref/tags/{quote(tag, safe='') }"
        status, _, _ = self._api("GET", path, repository=repository, expected=(200, 404))
        return status == 200

    def release_exists(self, tag: str) -> bool:
        path = f"/repos/{PRODUCT_REPOSITORY}/releases/tags/{quote(tag, safe='')}"
        status, _, body = self._api("GET", path, repository=PRODUCT_REPOSITORY,
                                    expected=(200, 404))
        if status == 200:
            release = self._json(body, "Release")
            self._release_by_tag[tag] = release
            return True
        return False

    def asset_names(self, tag: str) -> set[str]:
        release = self._release_by_tag.get(tag)
        if not release:
            path = f"/repos/{PRODUCT_REPOSITORY}/releases/tags/{quote(tag, safe='')}"
            _, _, body = self._api("GET", path, repository=PRODUCT_REPOSITORY)
            release = self._json(body, "Release")
        names = set()
        for item in release.get("assets", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise TransportError("Existing release has malformed asset metadata.")
            names.add(item["name"])
        return names

    def source_tag_exists(self, tag: str) -> bool:
        path = f"/repos/{SOURCE_REPOSITORY}/git/ref/tags/{quote(tag, safe='')}"
        status, _, _ = self._api("GET", path, repository=SOURCE_REPOSITORY,
                                 expected=(200, 404))
        return status == 200

    def pages_config(self) -> dict:
        status, _, body = self._api("GET", f"/repos/{PRODUCT_REPOSITORY}/pages",
                                    repository=PRODUCT_REPOSITORY, expected=(200, 404))
        if status == 404:
            raise TransportError("GitHub Pages is not configured; publication stopped before writes.")
        value = self._json(body, "Pages configuration")
        source = value.get("source")
        page_url = value.get("html_url")
        if (value.get("public") is not True or not isinstance(source, dict)
                or not isinstance(source.get("branch"), str) or not source["branch"]
                or source.get("path") not in {"/", "/docs"}
                or not isinstance(page_url, str)
                or page_url.rstrip("/") != PAGES_ROOT.rstrip("/")):
            raise TransportError("Configured Pages site does not match the public FishGram Pages contract.")
        self._pages = value
        return value

    def get_page(self, path: str) -> bytes | None:
        self._safe_page_path(path)
        if self._pages is None:
            self.pages_config()
        url = PAGES_ROOT + quote(path, safe="/")
        try:
            status, _, body = self._request("GET", url,
                                            {"Accept": "application/octet-stream",
                                             "User-Agent": "FishGram-release-publisher"},
                                            None, self._timeout)
        except Exception as error:
            raise TransportError("Public Pages read-back failed; response details were suppressed.") from error
        if status == 404:
            return None
        if status != 200:
            raise TransportError(f"Public Pages returned HTTP {status} during read-back.")
        return body

    @staticmethod
    def _safe_page_path(path: str) -> None:
        if (not isinstance(path, str) or not path or path.startswith("/")
                or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise TransportError("Pages path is malformed.")

    def get_index(self, path: str) -> dict | None:
        data = self.get_page(path)
        if data is None:
            return None
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransportError("Current public channel index is invalid JSON.") from error
        if not isinstance(value, dict):
            raise TransportError('Current public channel index must be a JSON object.')
        return value

    def _contents_path(self, path: str) -> str:
        self._safe_page_path(path)
        if self._pages is None:
            self.pages_config()
        prefix = "docs/" if self._pages["source"]["path"] == "/docs" else ""
        content_path = quote(prefix + path, safe="/")
        branch = quote(self._pages["source"]["branch"], safe="")
        return f"/repos/{PRODUCT_REPOSITORY}/contents/{content_path}?ref={branch}"

    def put_page(self, path: str, data: bytes) -> None:
        self._safe_page_path(path)
        if not isinstance(data, bytes):
            raise TransportError("Pages content must be bytes.")
        content_path = self._contents_path(path)
        get_path = content_path.split("?", 1)[0]
        query = content_path.split("?", 1)[1]
        status, _, body = self._api("GET", get_path + "?" + query,
                                    repository=PRODUCT_REPOSITORY, expected=(200, 404))
        existing_sha = None
        if status == 200:
            existing_sha = self._json(body, "Pages content").get("sha")
            if not isinstance(existing_sha, str) or not existing_sha:
                raise TransportError("Pages contents API omitted the existing file SHA.")
        payload = {"message": "Publish verified FishGram update metadata",
                   "content": base64.b64encode(data).decode("ascii"),
                   "branch": self._pages["source"]["branch"]}
        if existing_sha is not None:
            payload["sha"] = existing_sha
        self._api("PUT", get_path + "?" + query, repository=PRODUCT_REPOSITORY,
                  payload=payload, expected=(201, 200))

    def create_source_tag(self, tag: str, target: str) -> None:
        if self.source_tag_exists(tag):
            raise TransportError("Source repository tag already exists; source refs are immutable.")
        if len(target) != 40 or any(char not in "0123456789abcdef" for char in target):
            raise TransportError("Source repository tag target must be a full commit SHA.")
        tag_object = self._api("POST", f"/repos/{SOURCE_REPOSITORY}/git/tags",
                               repository=SOURCE_REPOSITORY,
                               payload={"tag": tag, "message": "FishGram release " + tag,
                                        "object": target, "type": "commit"},
                               source_write=True, expected=(201,))
        value = self._json(tag_object[2], "Source tag")
        sha = value.get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            raise TransportError("GitHub did not return the created source tag object SHA.")
        self._api("POST", f"/repos/{SOURCE_REPOSITORY}/git/refs",
                  repository=SOURCE_REPOSITORY,
                  payload={"ref": "refs/tags/" + tag, "sha": sha},
                  source_write=True, expected=(201,))

    def create_release(self, tag: str, target: str, title: str, body: str,
                       *, prerelease: bool) -> None:
        _, _, response = self._api("POST", f"/repos/{PRODUCT_REPOSITORY}/releases",
                                   repository=PRODUCT_REPOSITORY,
                                   payload={"tag_name": tag, "target_commitish": target,
                                            "name": title, "body": body, "draft": True,
                                            "prerelease": prerelease,
                                            "generate_release_notes": False}, expected=(201,))
        release = self._json(response, "Draft release")
        upload_url = release.get("upload_url")
        parsed = urlsplit(upload_url or "")
        expected_prefix = f"/repos/{PRODUCT_REPOSITORY}/releases/{release.get('id')}/assets"
        if (type(release.get("id")) is not int or release.get("tag_name") != tag
                or release.get("draft") is not True or parsed.scheme != "https"
                or parsed.hostname != "uploads.github.com" or parsed.port is not None
                or parsed.path != expected_prefix + "{"
                or not upload_url.endswith("{?name,label}")):
            raise TransportError("GitHub returned an unsafe or unexpected draft release upload URL.")
        self._release_by_tag[tag] = release

    def upload_asset(self, tag: str, name: str, data: bytes) -> None:
        release = self._release_by_tag.get(tag)
        if not release or release.get("draft") is not True:
            raise TransportError("Assets may be uploaded only to the current draft release.")
        if not name or "/" in name or "\\" in name:
            raise TransportError("Release asset name must be a basename.")
        base = release["upload_url"].split("{", 1)[0]
        url = base + "?" + urlencode({"name": name})
        parsed = urlsplit(url)
        if parsed.hostname != "uploads.github.com" or not parsed.path.startswith(
                f"/repos/{PRODUCT_REPOSITORY}/releases/{release['id']}/assets"):
            raise TransportError("Release asset upload URL escaped its approved release.")
        _, _, response = self._api("POST", parsed.path + "?" + parsed.query,
                                   repository=PRODUCT_REPOSITORY, raw=data,
                                   upload=True, expected=(201,))
        uploaded = self._json(response, "Uploaded release asset")
        if (uploaded.get("name") != name or uploaded.get("size") != len(data)
                or uploaded.get("state") != "uploaded"):
            raise TransportError("GitHub did not confirm the exact uploaded release asset.")
        release.setdefault("assets", []).append(uploaded)

    def publish_release(self, tag: str) -> None:
        release = self._release_by_tag.get(tag)
        if not release or release.get("draft") is not True:
            raise TransportError("Only the current draft release can be published.")
        _, _, body = self._api("PATCH", f"/repos/{PRODUCT_REPOSITORY}/releases/{release['id']}",
                               repository=PRODUCT_REPOSITORY, payload={"draft": False}, expected=(200,))
        result = self._json(body, "Published release")
        if result.get("draft") is not False:
            raise TransportError("GitHub did not confirm publication of the draft release.")
        release["draft"] = False

    def download_asset(self, tag: str, name: str) -> bytes:
        if not name or "/" in name or "\\" in name:
            raise TransportError("Release asset name must be a basename.")
        url = (f"https://github.com/{PRODUCT_REPOSITORY}/releases/download/"
               f"{quote(tag, safe='')}/{quote(name, safe='')}")
        try:
            status, _, body = self._request("GET", url,
                                            {"Accept": "application/octet-stream",
                                             "User-Agent": "FishGram-release-publisher"},
                                            None, self._timeout)
        except Exception as error:
            raise TransportError("Public release asset download failed; response details were suppressed.") from error
        if status != 200:
            raise TransportError(f"Public release asset returned HTTP {status} during verification.")
        return body
