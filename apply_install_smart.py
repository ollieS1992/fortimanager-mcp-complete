"""Make install_package self-correcting on vdom mismatches.

Adds:
- FortiManagerClient.get_package_scope(adom, pkg) -> list of scope members
- system_tools.install_package now reconciles caller's devices against
  the package's actual scope, correcting vdoms automatically.
"""

from pathlib import Path
import sys

CLIENT_PATH = Path("src/fortimanager_mcp/api/client.py")
TOOLS_PATH = Path("src/fortimanager_mcp/tools/system_tools.py")


def edit_client(content: str) -> str:
    """Add get_package_scope method to FortiManagerClient."""
    if "async def get_package_scope" in content:
        print("client.py: get_package_scope already present")
        return content

    ANCHOR = '''    async def get_package(
        self,
        adom: str,
        pkg: str,
        loadsub: int = 0,
    ) -> dict[str, Any]:
        """Get policy package details.

        FNDN: GET /pm/pkg/adom/{adom}/{pkg}
        """
        return await self.get(f"/pm/pkg/adom/{adom}/{pkg}", loadsub=loadsub)'''

    NEW = ANCHOR + '''

    async def get_package_scope(self, adom: str, pkg: str) -> list[dict[str, str]]:
        """Get the scope members (devices and vdoms) bound to a policy package.

        Returns a list of {"name": <device>, "vdom": <vdom>} dicts.
        Returns an empty list if the package has no scope members or
        the field is missing/malformed.
        """
        try:
            details = await self.get(
                f"/pm/pkg/adom/{adom}/{pkg}",
                option=["scope member"],
            )
            if not isinstance(details, dict):
                return []
            scope = details.get("scope member") or []
            if not isinstance(scope, list):
                return []
            normalised: list[dict[str, str]] = []
            for entry in scope:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                vdom = entry.get("vdom")
                if isinstance(name, str) and isinstance(vdom, str):
                    normalised.append({"name": name, "vdom": vdom})
            return normalised
        except Exception as e:
            logger.warning(
                f"Could not retrieve scope for package {pkg} in ADOM {adom}: {e}"
            )
            return []'''

    if ANCHOR not in content:
        sys.exit("ERROR: Could not find get_package method to anchor against")
    content = content.replace(ANCHOR, NEW)
    print("client.py: added get_package_scope method")
    return content


def edit_tools(content: str) -> str:
    """Replace install_package tool body with scope-reconciling version."""
    if "_reconcile_install_scope" in content:
        print("system_tools.py: install_package already updated")
        return content

    OLD_BODY = '''    try:
        client = _get_client()

        flags = ["preview"] if preview else ["none"]

        result = await client.install_package(
            adom=adom,
            pkg=package,
            scope=devices,
            flags=flags,
        )

        task_id = result.get("task")
        return {
            "status": "success",
            "task_id": task_id,
            "preview": preview,
            "message": f"Installation {'preview ' if preview else ''}started, task ID: {task_id}",
        }
    except Exception as e:
        logger.error(f"Failed to install package {package}: {e}")
        return {"status": "error", "message": str(e)}'''

    NEW_BODY = '''    try:
        client = _get_client()

        flags = ["preview"] if preview else ["none"]

        # Reconcile the caller-supplied devices against the package's actual
        # scope. FMG is fussy: an install with the wrong vdom for a given
        # device returns a misleading "no write permission" rather than a
        # clear scope error. We look up the truth from the package and
        # correct mismatches transparently.
        actual_scope = await client.get_package_scope(adom=adom, pkg=package)
        scope_to_use, corrections = _reconcile_install_scope(devices, actual_scope)
        for note in corrections:
            logger.info(f"install_package scope correction: {note}")

        result = await client.install_package(
            adom=adom,
            pkg=package,
            scope=scope_to_use,
            flags=flags,
        )

        task_id = result.get("task")
        message = (
            f"Installation {'preview ' if preview else ''}started, task ID: {task_id}"
        )
        if corrections:
            message += f" (auto-corrected scope: {'; '.join(corrections)})"

        return {
            "status": "success",
            "task_id": task_id,
            "preview": preview,
            "scope_used": scope_to_use,
            "scope_corrections": corrections,
            "message": message,
        }
    except Exception as e:
        logger.error(f"Failed to install package {package}: {e}")
        return {"status": "error", "message": str(e)}


def _reconcile_install_scope(
    requested: list[dict[str, str]],
    actual: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Reconcile a requested install scope against a package's true scope.

    Rules:
    - If `requested` is empty, use `actual` as-is (default behaviour).
    - For each requested device, look up the device name in `actual`:
        * If the vdom matches, pass through unchanged.
        * If the vdom mismatches, replace with the actual vdom and record
          a correction note.
        * If the device is not in actual scope at all, pass through unchanged
          (let FMG return its own error).
    - If `actual` is empty, pass `requested` through unchanged.

    Returns (scope_to_use, list_of_correction_notes).
    """
    corrections: list[str] = []

    if not requested:
        if actual:
            corrections.append(
                f"no devices supplied, using package scope ({len(actual)} member(s))"
            )
            return actual, corrections
        return [], corrections

    if not actual:
        return requested, corrections

    # Index actual scope by device name
    actual_by_name: dict[str, str] = {entry["name"]: entry["vdom"] for entry in actual}

    reconciled: list[dict[str, str]] = []
    for entry in requested:
        name = entry.get("name", "")
        requested_vdom = entry.get("vdom", "")
        actual_vdom = actual_by_name.get(name)
        if actual_vdom is not None and actual_vdom != requested_vdom:
            corrections.append(
                f"{name}: vdom '{requested_vdom}' -> '{actual_vdom}'"
            )
            reconciled.append({"name": name, "vdom": actual_vdom})
        else:
            reconciled.append({"name": name, "vdom": requested_vdom})

    return reconciled, corrections'''

    if OLD_BODY not in content:
        sys.exit(
            "ERROR: Could not find install_package body to replace. The file "
            "may have been modified since the last edit, or the function shape "
            "is different than expected."
        )
    content = content.replace(OLD_BODY, NEW_BODY)
    print("system_tools.py: install_package now reconciles scope")
    return content


def main() -> None:
    if not CLIENT_PATH.exists() or not TOOLS_PATH.exists():
        sys.exit("ERROR: Required files not found. Run from project root.")

    client_orig = CLIENT_PATH.read_text(encoding="utf-8")
    client_new = edit_client(client_orig)
    if client_new != client_orig:
        CLIENT_PATH.write_text(client_new, encoding="utf-8")

    tools_orig = TOOLS_PATH.read_text(encoding="utf-8")
    tools_new = edit_tools(tools_orig)
    if tools_new != tools_orig:
        TOOLS_PATH.write_text(tools_new, encoding="utf-8")

    if client_new == client_orig and tools_new == tools_orig:
        print("\nNo changes needed; both files already up to date.")
    else:
        print("\nDone. Changes applied.")


if __name__ == "__main__":
    main()