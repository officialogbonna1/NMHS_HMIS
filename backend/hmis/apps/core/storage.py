"""
Uploaded patient documents in Cloudinary, privately — production only.

This is Django's `STORAGES["default"]` in production when
`DJANGO_MEDIA_STORAGE=cloudinary` (hmis/environment.py `media_storage`). Every
`FileField` — the Medical Tests & Diagnostics record that unit reports are
filed under, and the legacy diagnostics attachment — saves and links through
it, so no upload form, serializer, model or migration changed. Development
keeps the local `media/` directory and never imports Cloudinary.

**Private, and time-limited, by construction.**

* Every file is uploaded as `resource_type="raw"` with **`type="authenticated"`**.
  Cloudinary refuses an authenticated asset to anybody without a signature:
  its ordinary CDN address does not work.
* `url()` never returns a CDN address. It returns a **private download URL**
  (Cloudinary's `private_download_url`, the `/download` endpoint of its API)
  carrying `expires_at`: Cloudinary checks the expiry itself, on every request,
  and the response is not cached on its CDN. A plain *signed* CDN URL was
  deliberately not used — Cloudinary's signatures on those never expire.
* A link is only ever made by `url()`, and `url()` is only called by the
  serializers and views that already decide who may read the record — so the
  existing permissions decide who gets a link, exactly as before.
* `raw` keeps every format the record accepts (images, PDFs, Word, Excel)
  byte-for-byte, with its extension in the public ID, and lets nothing be
  transformed.

The link carries the cloud name, the **API key** (an identifier, not a
credential) and a signature; the **API secret** never leaves this process.
Credentials are passed to each SDK call rather than set globally, so nothing
else in the process can upload with them.
"""
import posixpath
import time
import uuid
from urllib.request import urlopen

from django.core.files.base import ContentFile
from django.core.files.storage import Storage

RESOURCE_TYPE = "raw"
DELIVERY_TYPE = "authenticated"


class PrivateCloudinaryStorage(Storage):
    def __init__(self, cloud_name="", api_key="", api_secret="", folder="nmhs-hmis/media",
                 link_seconds=900):
        self.cloud_name = cloud_name
        self.api_key = api_key
        self.api_secret = api_secret
        self.folder = folder.strip("/")
        self.link_seconds = int(link_seconds)

    def __repr__(self):  # never print the secret
        return f"<PrivateCloudinaryStorage cloud={self.cloud_name!r} folder={self.folder!r}>"

    # ------------------------------------------------------------ internals
    def _auth(self):
        return {"cloud_name": self.cloud_name, "api_key": self.api_key,
                "api_secret": self.api_secret}

    def _public_id(self, name):
        """`medical_tests/2026/10/scan_ab12cd34.pdf` → `<folder>/medical_tests/…pdf`.
        Raw assets keep their extension in the public ID."""
        name = name.replace("\\", "/").lstrip("/")
        return f"{self.folder}/{name}" if self.folder else name

    def _options(self, **extra):
        return {"resource_type": RESOURCE_TYPE, "type": DELIVERY_TYPE, **self._auth(), **extra}

    # ------------------------------------------------------------- naming
    def get_available_name(self, name, max_length=None):
        """
        A unique name without asking Cloudinary: a short random suffix before
        the extension. Two uploads of `scan.pdf` can never overwrite each other,
        and saving costs one API call (the upload), not a lookup first.
        """
        directory, base = posixpath.split(name.replace("\\", "/"))
        root, ext = posixpath.splitext(base)
        suffix = f"_{uuid.uuid4().hex[:8]}"
        candidate = posixpath.join(directory, f"{root}{suffix}{ext}")
        if max_length and len(candidate) > max_length:
            keep = max(1, len(root) - (len(candidate) - max_length))
            candidate = posixpath.join(directory, f"{root[:keep]}{suffix}{ext}")
        return candidate

    # ------------------------------------------------------------ storage API
    def _save(self, name, content):
        import cloudinary.uploader

        if hasattr(content, "seek"):
            content.seek(0)
        cloudinary.uploader.upload(
            getattr(content, "file", None) or content,
            **self._options(public_id=self._public_id(name),
                            overwrite=False, use_filename=False, unique_filename=False),
        )
        return name

    def url(self, name):
        """A private download link that Cloudinary refuses after `link_seconds`."""
        import cloudinary.utils

        return cloudinary.utils.private_download_url(
            self._public_id(name), "",
            expires_at=int(time.time()) + self.link_seconds,
            **self._options(),
        )

    def _open(self, name, mode="rb"):
        if "w" in mode or "a" in mode:
            raise ValueError("Cloudinary documents are opened read-only.")
        with urlopen(self.url(name), timeout=30) as response:  # noqa: S310 — https API URL
            return ContentFile(response.read(), name=posixpath.basename(name))

    def exists(self, name):
        import cloudinary.api
        from cloudinary.exceptions import NotFound

        try:
            cloudinary.api.resource(self._public_id(name), **self._options())
        except NotFound:
            return False
        return True

    def size(self, name):
        import cloudinary.api

        return cloudinary.api.resource(self._public_id(name), **self._options())["bytes"]

    def delete(self, name):
        import cloudinary.uploader

        cloudinary.uploader.destroy(self._public_id(name), **self._options(invalidate=True))
