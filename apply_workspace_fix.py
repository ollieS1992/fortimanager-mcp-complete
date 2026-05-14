"""Apply workspace-lock fixes to fortimanager-mcp client.py.

This script makes the following changes to src/fortimanager_mcp/api/client.py:
1. Adds check_adom_workspace=False to the username/password constructor
2. Adds the _ADOMLockContext class above FortiManagerClient
3. Adds is_workspace_enabled and adom_lock methods to FortiManagerClient
4. Adds _needs_workspace_lock and _extract_adom_from_url helpers
5. Adds _do_write helper that auto-acquires locks for /pm/ writes
6. Modifies add/set/update/delete/move methods to use _do_write
"""

from pathlib import Path
import sys

CLIENT_PATH = Path("src/fortimanager_mcp/api/client.py")


def apply_edits(content: str) -> str:
    """Apply all six edits, idempotently. Returns updated content."""

    # Edit 1: check_adom_workspace=False on username/password constructor
    OLD_1 = """            elif self.username and self.password:
                self._fmg = FortiManager(
                    self.host,
                    self.username,
                    self.password,
                    debug=False,
                    use_ssl=True,
                    verify_ssl=self.verify_ssl,
                    timeout=self.timeout,
                )"""
    NEW_1 = """            elif self.username and self.password:
                self._fmg = FortiManager(
                    self.host,
                    self.username,
                    self.password,
                    debug=False,
                    use_ssl=True,
                    verify_ssl=self.verify_ssl,
                    timeout=self.timeout,
                    check_adom_workspace=False,
                )"""
    if NEW_1 in content:
        print("Edit 1 already applied (check_adom_workspace=False on user/pass)")
    elif OLD_1 in content:
        content = content.replace(OLD_1, NEW_1)
        print("Applied Edit 1: check_adom_workspace=False on user/pass constructor")
    else:
        sys.exit("ERROR: Could not find user/pass constructor block for Edit 1")

    # Edit 2: _ADOMLockContext class added above FortiManagerClient
    if "class _ADOMLockContext:" in content:
        print("Edit 2 already applied (_ADOMLockContext class)")
    else:
        ANCHOR_2 = "class FortiManagerClient:"
        ADOM_LOCK_CONTEXT = '''class _ADOMLockContext:
    """Async context manager for workspace-mode ADOM locking.

    Used by FortiManagerClient.adom_lock(adom). When workspace mode is
    enabled, write operations to /pm/* require an ADOM lock to be held.
    This context manager acquires the lock on entry and commits or aborts
    on exit. When workspace mode is disabled, this is a no-op passthrough.
    """

    def __init__(self, client: "FortiManagerClient", adom: str) -> None:
        self._client = client
        self._adom = adom
        self._locked = False

    async def __aenter__(self) -> "_ADOMLockContext":
        if await self._client.is_workspace_enabled():
            logger.debug(f"Locking ADOM {self._adom} for write")
            await self._client.lock_adom(self._adom)
            self._locked = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self._locked:
            return
        try:
            if exc_type is None:
                logger.debug(f"Committing changes to ADOM {self._adom}")
                await self._client.commit_adom(self._adom)
            else:
                logger.debug(
                    f"Aborting changes to ADOM {self._adom} due to {exc_type.__name__}"
                )
        finally:
            try:
                await self._client.unlock_adom(self._adom)
            except Exception as e:
                logger.warning(f"Failed to unlock ADOM {self._adom}: {e}")
            self._locked = False


'''
        if ANCHOR_2 not in content:
            sys.exit("ERROR: Could not find 'class FortiManagerClient:' for Edit 2")
        content = content.replace(ANCHOR_2, ADOM_LOCK_CONTEXT + ANCHOR_2)
        print("Applied Edit 2: _ADOMLockContext class")

    # Edit 3: is_workspace_enabled and adom_lock methods on FortiManagerClient
    if "async def is_workspace_enabled" in content:
        print("Edit 3 already applied (is_workspace_enabled and adom_lock)")
    else:
        ANCHOR_3 = """    # =========================================================================
    # Workspace Mode (ADOM Locking)
    # =========================================================================

    async def lock_adom(self, adom: str) -> dict[str, Any]:"""
        DETECTION_AND_LOCK = '''    # =========================================================================
    # Workspace Mode Detection & Lock Context Manager
    # =========================================================================

    async def is_workspace_enabled(self) -> bool:
        """Check whether workspace mode is enabled on FortiManager.

        Caches the result on the instance after first call. Returns False
        if detection fails, so failures don't break operations on FMG
        instances that don't use workspace mode.
        """
        if hasattr(self, "_workspace_enabled"):
            return self._workspace_enabled

        try:
            result = await self.get(
                "/cli/global/system/global",
                fields=["workspace-mode"],
            )
            mode: Any = "disabled"
            if isinstance(result, dict):
                mode = result.get("workspace-mode", "disabled")
            elif isinstance(result, list) and result:
                first = result[0]
                if isinstance(first, dict):
                    mode = first.get("workspace-mode", "disabled")
            # FMG can return mode as a string ("disabled"/"normal"/"workflow")
            # or an integer (0=disabled, non-zero=enabled).
            if isinstance(mode, int):
                self._workspace_enabled = mode != 0
            else:
                self._workspace_enabled = mode != "disabled"
            logger.info(
                f"Workspace mode detected: {mode} (enabled={self._workspace_enabled})"
            )
        except Exception as e:
            logger.warning(
                f"Could not detect workspace mode, assuming disabled: {e}"
            )
            self._workspace_enabled = False

        return self._workspace_enabled

    def adom_lock(self, adom: str) -> "_ADOMLockContext":
        """Return an async context manager that locks an ADOM for writes.

        When workspace mode is enabled, write operations against /pm/*
        require an ADOM lock. This context manager acquires the lock on
        entry, commits and unlocks on clean exit, and unlocks (without
        committing) if an exception is raised. No-op when workspace
        mode is disabled.
        """
        return _ADOMLockContext(self, adom)

    # =========================================================================
    # Workspace Mode (ADOM Locking)
    # =========================================================================

    async def lock_adom(self, adom: str) -> dict[str, Any]:'''
        if ANCHOR_3 not in content:
            sys.exit("ERROR: Could not find Workspace Mode anchor for Edit 3")
        content = content.replace(ANCHOR_3, DETECTION_AND_LOCK)
        print("Applied Edit 3: is_workspace_enabled and adom_lock methods")

    # Edit 4: URL helper methods + _do_write helper, plus rewrite of add/set/update/delete/move
    if "_needs_workspace_lock" in content:
        print("Edit 4 already applied (URL helpers and _do_write rewrites)")
    else:
        ANCHOR_4 = """    # =========================================================================
    # Generic Operations
    # =========================================================================

    async def get(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute GET request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.get(url, **kwargs)
        return self._handle_response(code, response, f"GET {url}")

    async def add(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute ADD request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.add(url, **kwargs)
        return self._handle_response(code, response, f"ADD {url}")

    async def set(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute SET request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.set(url, **kwargs)
        return self._handle_response(code, response, f"SET {url}")

    async def update(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute UPDATE request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.update(url, **kwargs)
        return self._handle_response(code, response, f"UPDATE {url}")

    async def delete(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute DELETE request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.delete(url, **kwargs)
        return self._handle_response(code, response, f"DELETE {url}")

    async def execute(self, url: str, **kwargs: Any) -> Any:
        \"\"\"Execute EXEC request.\"\"\"
        fmg = self._ensure_connected()
        code, response = fmg.execute(url, **kwargs)
        return self._handle_response(code, response, f"EXEC {url}")

    async def move(self, url: str, option: str, target: str) -> Any:
        \"\"\"Execute MOVE request.

        Args:
            url: The URL of the object to move
            option: \"before\" or \"after\"
            target: Target object ID (as string)
        \"\"\"
        fmg = self._ensure_connected()
        # Pass as dict in args (not kwargs) so it merges at top level, not in 'data'
        code, response = fmg.move(url, {"option": option, "target": target})
        return self._handle_response(code, response, f"MOVE {url}")"""

        REPLACEMENT_4 = '''    # =========================================================================
    # Internal: Workspace Lock Awareness for Writes
    # =========================================================================

    @staticmethod
    def _needs_workspace_lock(url: str) -> bool:
        """Return True if writes to this URL require an ADOM workspace lock.

        FortiManager requires explicit ADOM locking for /pm/config/* and
        /pm/pkg/* writes when workspace mode is enabled. Other endpoints
        (notably /dvmdb/* and /sys/*) do not.
        """
        return url.startswith("/pm/config/") or url.startswith("/pm/pkg/")

    @staticmethod
    def _extract_adom_from_url(url: str) -> str | None:
        """Extract ADOM name from URLs that contain /adom/{name}/ segments.

        Returns None if the URL has no ADOM segment, in which case no
        automatic lock will be attempted.
        """
        parts = url.split("/")
        try:
            idx = parts.index("adom")
            return parts[idx + 1]
        except (ValueError, IndexError):
            return None

    async def _do_write(
        self,
        method_name: str,
        url: str,
        do_call: Any,
    ) -> Any:
        """Execute a write call, automatically wrapping it in an ADOM lock
        when the URL requires one and workspace mode is enabled.

        Args:
            method_name: Operation label for logging (e.g. "ADD", "UPDATE").
            url: Target URL.
            do_call: Zero-argument callable that performs the actual pyfmg
                call and returns (code, response).
        """
        adom = (
            self._extract_adom_from_url(url)
            if self._needs_workspace_lock(url)
            else None
        )
        if adom:
            async with self.adom_lock(adom):
                code, response = do_call()
                return self._handle_response(code, response, f"{method_name} {url}")
        code, response = do_call()
        return self._handle_response(code, response, f"{method_name} {url}")

    # =========================================================================
    # Generic Operations
    # =========================================================================

    async def get(self, url: str, **kwargs: Any) -> Any:
        """Execute GET request."""
        fmg = self._ensure_connected()
        code, response = fmg.get(url, **kwargs)
        return self._handle_response(code, response, f"GET {url}")

    async def add(self, url: str, **kwargs: Any) -> Any:
        """Execute ADD request."""
        fmg = self._ensure_connected()
        return await self._do_write("ADD", url, lambda: fmg.add(url, **kwargs))

    async def set(self, url: str, **kwargs: Any) -> Any:
        """Execute SET request."""
        fmg = self._ensure_connected()
        return await self._do_write("SET", url, lambda: fmg.set(url, **kwargs))

    async def update(self, url: str, **kwargs: Any) -> Any:
        """Execute UPDATE request."""
        fmg = self._ensure_connected()
        return await self._do_write("UPDATE", url, lambda: fmg.update(url, **kwargs))

    async def delete(self, url: str, **kwargs: Any) -> Any:
        """Execute DELETE request."""
        fmg = self._ensure_connected()
        return await self._do_write("DELETE", url, lambda: fmg.delete(url, **kwargs))

    async def execute(self, url: str, **kwargs: Any) -> Any:
        """Execute EXEC request."""
        fmg = self._ensure_connected()
        code, response = fmg.execute(url, **kwargs)
        return self._handle_response(code, response, f"EXEC {url}")

    async def move(self, url: str, option: str, target: str) -> Any:
        """Execute MOVE request.

        Args:
            url: The URL of the object to move
            option: "before" or "after"
            target: Target object ID (as string)
        """
        fmg = self._ensure_connected()
        # Pass as dict in args (not kwargs) so it merges at top level, not in 'data'
        return await self._do_write(
            "MOVE",
            url,
            lambda: fmg.move(url, {"option": option, "target": target}),
        )'''

        if ANCHOR_4 not in content:
            sys.exit(
                "ERROR: Could not find Generic Operations anchor for Edit 4. "
                "The original methods may have been modified already, or the "
                "file is not at the expected baseline."
            )
        content = content.replace(ANCHOR_4, REPLACEMENT_4)
        print("Applied Edit 4: URL helpers, _do_write, and rewritten add/set/update/delete/move")

    return content


def main() -> None:
    if not CLIENT_PATH.exists():
        sys.exit(f"ERROR: {CLIENT_PATH} does not exist. Run from project root.")

    original = CLIENT_PATH.read_text(encoding="utf-8")
    updated = apply_edits(original)

    if updated == original:
        print("No changes needed; file is already up to date.")
        return

    CLIENT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n{CLIENT_PATH} updated.")


if __name__ == "__main__":
    main()