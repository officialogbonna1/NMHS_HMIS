"""
Settings for `collectstatic` while a container image is built — nothing else.

A Docker build on Railway receives none of the service's variables (Docker
isolates the build; Railway passes a variable only if the Dockerfile declares it
with ARG, which would also record it in the image's metadata). So the image is
built with **no** production configuration at all: no secret key, no database,
no Cloudinary credentials. Loading `hmis.settings` in that state gives the
development configuration, whose static-file storage writes no manifest — but
production serves static files through WhiteNoise's manifest storage, which
refuses to run without one.

This module is that development configuration with exactly one change: the
production static-file storage. `collectstatic` then writes the same hashed
files and `staticfiles.json` manifest production reads. It is used only by the
`RUN` line in `Dockerfile.api`; the running container uses `hmis.settings`, with
`DJANGO_ENV=production` and the real variables from Railway, unchanged.
"""
from hmis.settings import *  # noqa: F401,F403 — the development configuration, as is
from hmis.settings import STORAGES

STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
