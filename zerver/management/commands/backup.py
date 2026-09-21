import contextlib
import os
import platform
import re
import secrets
import stat
import tempfile
from argparse import ArgumentParser, RawTextHelpFormatter
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import CommandParser
from django.db import connection
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from scripts.lib.zulip_tools import TIMESTAMP_FORMAT, run
from version import ZULIP_VERSION
from zerver.lib.agent_backup import stage_agent_backup, verify_agent_backup_references
from zerver.lib.management import ZulipBaseCommand
from zerver.logging_handlers import try_git_describe
from zerver.models import agents


@dataclass
class OutputStaging:
    parent_fd: int | None
    directory_fd: int | None
    directory_name: str
    directory_identity: tuple[int, int]
    archive_fd: int | None


def _make_private_output_staging(destination: str) -> OutputStaging:
    path = Path(destination)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fd = -1
    archive_fd = -1
    try:
        while True:
            directory_name = f".{path.name}.{secrets.token_hex(16)}.partial"
            try:
                os.mkdir(directory_name, 0o700, dir_fd=parent_fd)
                break
            except FileExistsError:
                continue
        directory_fd = os.open(
            directory_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        info = os.fstat(directory_fd)
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_mode & 0o7000
        ):
            raise PermissionError("Backup staging directory is not private.")
        archive_fd = os.open(
            "archive.tar.gz",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(archive_fd, 0o600)
        return OutputStaging(
            parent_fd, directory_fd, directory_name, (info.st_dev, info.st_ino), archive_fd
        )
    except Exception:
        if archive_fd != -1:
            os.close(archive_fd)
        if directory_fd != -1:
            os.close(directory_fd)
        os.close(parent_fd)
        raise


def _remove_owned_output_staging(staging: OutputStaging) -> None:
    archive_fd = staging.archive_fd
    staging.archive_fd = None
    if archive_fd is not None:
        with contextlib.suppress(OSError):
            os.close(archive_fd)

    directory_fd = staging.directory_fd
    staging.directory_fd = None
    if directory_fd is not None:
        with contextlib.suppress(OSError):
            os.unlink("archive.tar.gz", dir_fd=directory_fd)
        with contextlib.suppress(OSError):
            os.close(directory_fd)

    parent_fd = staging.parent_fd
    staging.parent_fd = None
    if parent_fd is None:
        return
    try:
        info = os.stat(staging.directory_name, dir_fd=parent_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == staging.directory_identity:
            with contextlib.suppress(OSError):
                os.rmdir(staging.directory_name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(parent_fd)


def _publish_output_staging(staging: OutputStaging, destination: str) -> None:
    assert staging.archive_fd is not None
    assert staging.directory_fd is not None
    assert staging.parent_fd is not None
    os.fsync(staging.archive_fd)
    os.link(
        "archive.tar.gz",
        Path(destination).name,
        src_dir_fd=staging.directory_fd,
        dst_dir_fd=staging.parent_fd,
        follow_symlinks=False,
    )


class Command(ZulipBaseCommand):
    # Fix support for multi-line usage strings
    @override
    def create_parser(self, prog_name: str, subcommand: str, **kwargs: Any) -> CommandParser:
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.formatter_class = RawTextHelpFormatter
        return parser

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--output", help="Filename of output tarball")
        parser.add_argument("--skip-db", action="store_true", help="Skip database backup")
        parser.add_argument("--skip-uploads", action="store_true", help="Skip uploads backup")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        timestamp = timezone_now().strftime(TIMESTAMP_FORMAT)
        with ExitStack() as stack:
            tmp = stack.enter_context(
                tempfile.TemporaryDirectory(prefix=f"zulip-backup-{timestamp}-")
            )
            os.mkdir(os.path.join(tmp, "zulip-backup"))
            members = []
            paths = []

            with open(os.path.join(tmp, "zulip-backup", "zulip-version"), "w") as f:
                print(ZULIP_VERSION, file=f)
                git = try_git_describe()
                if git:
                    print(git, file=f)
            members.append("zulip-backup/zulip-version")

            with open(os.path.join(tmp, "zulip-backup", "os-version"), "w") as f:
                print(
                    "{ID} {VERSION_ID}".format(**platform.freedesktop_os_release()),
                    file=f,
                )
            members.append("zulip-backup/os-version")

            with open(os.path.join(tmp, "zulip-backup", "postgres-version"), "w") as f:
                pg_server_version = connection.cursor().connection.server_version
                major_pg_version = pg_server_version // 10000
                print(pg_server_version, file=f)
            members.append("zulip-backup/postgres-version")

            if settings.DEVELOPMENT:
                members.append(
                    os.path.join(settings.DEPLOY_ROOT, "zproject", "dev-secrets.conf"),
                )
                paths.append(
                    ("zproject", os.path.join(settings.DEPLOY_ROOT, "zproject")),
                )
            else:
                members.append("/etc/zulip")
                paths.append(("settings", "/etc/zulip"))

            if not options["skip_db"]:
                pg_dump_command = [
                    f"/usr/lib/postgresql/{major_pg_version}/bin/pg_dump",
                    "--format=directory",
                    "--file=" + os.path.join(tmp, "zulip-backup", "database"),
                    "--username=" + settings.DATABASES["default"]["USER"],
                    "--dbname=" + settings.DATABASES["default"]["NAME"],
                    "--no-password",
                ]
                if settings.DATABASES["default"].get("HOST"):
                    pg_dump_command += ["--host=" + settings.DATABASES["default"]["HOST"]]
                if settings.DATABASES["default"].get("PORT"):
                    pg_dump_command += ["--port=" + str(settings.DATABASES["default"]["PORT"])]

                os.environ["PGPASSWORD"] = settings.DATABASES["default"]["PASSWORD"]

                run(
                    pg_dump_command,
                    cwd=tmp,
                )
                members.append("zulip-backup/database")

            artifact_root = getattr(settings, "AGENT_ARTIFACT_ROOT", None)
            keyring_file = getattr(settings, "AGENT_SECRET_MASTER_KEY_FILE", None)
            artifact_references = list(
                agents.AgentArtifact.objects.filter(unavailable_at__isnull=True).values_list(
                    "storage_ref", "size", "checksum"
                )
            )
            secret_key_ids = set(agents.AgentSecret.objects.values_list("key_id", flat=True))
            staged_agent = stage_agent_backup(
                Path(tmp) / "zulip-backup",
                artifact_root=Path(artifact_root) if artifact_root else None,
                keyring_file=Path(keyring_file) if keyring_file else None,
                require_artifacts=bool(artifact_references),
                require_keyring=bool(secret_key_ids),
            )
            if staged_agent is not None:
                verify_agent_backup_references(
                    staged_agent, artifacts=artifact_references, secret_key_ids=secret_key_ids
                )
                members.append("zulip-backup/agent")

            if (
                not options["skip_uploads"]
                and settings.LOCAL_UPLOADS_DIR is not None
                and os.path.exists(
                    os.path.join(settings.DEPLOY_ROOT, settings.LOCAL_UPLOADS_DIR),
                )
            ):
                members.append(
                    os.path.join(settings.DEPLOY_ROOT, settings.LOCAL_UPLOADS_DIR),
                )
                paths.append(
                    (
                        "uploads",
                        os.path.join(settings.DEPLOY_ROOT, settings.LOCAL_UPLOADS_DIR),
                    ),
                )

            assert not any("|" in name or "|" in path for name, path in paths)
            transform_args = [
                r"--transform=s|^{}(/.*)?$|zulip-backup/{}\1|x".format(
                    re.escape(path),
                    name.replace("\\", r"\\"),
                )
                for name, path in paths
            ]

            tarball_path: str | None = None
            output_staging: OutputStaging | None = None
            try:
                if options["output"] is None:
                    tarball_path = stack.enter_context(
                        tempfile.NamedTemporaryFile(
                            prefix=f"zulip-backup-{timestamp}-",
                            suffix=".tar.gz",
                            delete=False,
                        )
                    ).name
                else:
                    output_staging = _make_private_output_staging(options["output"])
                    tarball_path = "-"

                tar_command = [
                    "tar",
                    f"--directory={tmp}",
                    "-cPhzf",
                    tarball_path,
                    *transform_args,
                    "--",
                    *members,
                ]
                if output_staging is None:
                    run(tar_command)
                else:
                    assert output_staging.archive_fd is not None
                    with os.fdopen(output_staging.archive_fd, "wb", closefd=False) as archive:
                        run(tar_command, stdout=archive)
                if output_staging is not None:
                    _publish_output_staging(output_staging, options["output"])
                    completed_staging = output_staging
                    output_staging = None
                    _remove_owned_output_staging(completed_staging)
                    tarball_path = options["output"]
                print(f"Backup tarball written to {tarball_path}")
            except BaseException:
                if output_staging is not None:
                    _remove_owned_output_staging(output_staging)
                elif options["output"] is None and tarball_path is not None:
                    os.unlink(tarball_path)
                raise
