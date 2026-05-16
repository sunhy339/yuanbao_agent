"""Shell command compatibility adapter — translates bash idioms to PowerShell."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TransformRecord:
    """One transformation applied during adaptation."""

    pattern: str
    original: str
    replacement: str


@dataclass
class AdaptationResult:
    """Result of adapting a command for a target shell."""

    original: str
    adapted: str
    transforms: list[TransformRecord] = field(default_factory=list)
    confidence: str = "high"  # "high" | "medium"
    shell_target: str = "passthrough"  # "powershell" | "passthrough"


class CommandCompatAdapter:
    """Detect and adapt bash-style commands for PowerShell compatibility."""

    # Patterns that indicate native PowerShell — do NOT adapt these
    _PS_INDICATOR_RE = re.compile(r"^\s*[\$\-]|::|\[\]")

    def __init__(self) -> None:
        self._rules: list[tuple[re.Pattern, str, str, str]] = [
            # (compiled_re, group_to_transform, replacement_template, confidence)
            # Rule order matters — earlier rules are applied first
        ]
        self._build_rules()

    def _build_rules(self) -> None:
        """Build the ordered rule list for bash→PowerShell transforms."""

        # 1. &&  →  ; if ($?) { ... }
        self._rules.append((
            re.compile(r"&&"),
            "chain_and",
            r"; if ($?) { ",
            "high",
        ))

        # 2. ||  →  ; if (-not $?) { ... }
        self._rules.append((
            re.compile(r"\|\|"),
            "chain_or",
            r"; if (-not $?) { ",
            "high",
        ))

        # 3. 2>/dev/null  →  2>$null
        self._rules.append((
            re.compile(r"2>/dev/null"),
            "redirect_stderr_null",
            "2>$null",
            "high",
        ))

        # 4. 1>/dev/null  →  1>$null
        self._rules.append((
            re.compile(r"1>/dev/null"),
            "redirect_stdout_null",
            "1>$null",
            "high",
        ))

        # 5. &>/dev/null  →  *>$null
        self._rules.append((
            re.compile(r"&>/dev/null"),
            "redirect_all_null",
            "*>$null",
            "high",
        ))

        # 6. >out 2>&1  →  standard redirect (PowerShell handles natively)
        #    We leave these as-is since PowerShell supports them

        # 7. $VAR (env variable) → $env:VAR  (medium confidence)
        self._rules.append((
            re.compile(r"(?<!\w)\$(?![\{\(]|null\b|true\b|false\b|_\b|matches\b)(\w+)", re.IGNORECASE),
            "env_var",
            r"$env:\1",
            "medium",
        ))

        # 8. ${VAR} → $env:VAR
        self._rules.append((
            re.compile(r"\$\{(\w+)\}"),
            "env_var_braced",
            r"$env:\1",
            "medium",
        ))

        # 9. export KEY=VAL → $env:KEY = "VAL"
        self._rules.append((
            re.compile(r"\bexport\s+(\w+)=(\S+)"),
            "export",
            None,  # special handling
            "medium",
        ))

        # 10. source file → . file
        self._rules.append((
            re.compile(r"\bsource\s+"),
            "source",
            ". ",
            "high",
        ))

        # 11. backslash line continuation → backtick
        self._rules.append((
            re.compile(r"\\\n"),
            "line_cont",
            "`\n",
            "high",
        ))

    def adapt(self, command: str, shell: str) -> AdaptationResult:
        """Adapt *command* for the given *shell*.

        Returns an AdaptationResult with the adapted command and metadata.
        If shell is not "powershell", returns a passthrough result unchanged.
        """
        if shell != "powershell":
            return AdaptationResult(
                original=command,
                adapted=command,
                shell_target="passthrough",
            )

        adapted = command
        transforms: list[TransformRecord] = []
        overall_confidence = "high"

        # Apply rules in order
        for pattern, rule_name, replacement, confidence in self._rules:
            if rule_name == "export":
                # Special handling for export — needs capture groups
                for m in pattern.finditer(command):
                    # Only match if the export line isn't inside quotes
                    original_fragment = m.group(0)
                    key = m.group(1)
                    val = m.group(2)
                    repl = f'$env:{key} = "{val}"'
                    transforms.append(TransformRecord(
                        pattern=rule_name,
                        original=original_fragment,
                        replacement=repl,
                    ))
                    if confidence == "medium":
                        overall_confidence = "medium"
                # Replace in adapted string
                adapted = pattern.sub(lambda m: f'$env:{m.group(1)} = "{m.group(2)}"', adapted)
            elif rule_name in ("chain_and", "chain_or"):
                # && and || need special wrapping: the right side gets wrapped in if($?) block
                matches = list(pattern.finditer(adapted))
                if matches:
                    for m in matches:
                        transforms.append(TransformRecord(
                            pattern=rule_name,
                            original=m.group(0),
                            replacement=replacement,
                        ))
                        if confidence == "medium":
                            overall_confidence = "medium"
                # Need to close the if-blocks — add closing } after each adapted segment
                # This is complex; we handle it by reconstructing from parts
            else:
                matches = list(pattern.finditer(command))
                if matches:
                    for m in matches:
                        transforms.append(TransformRecord(
                            pattern=rule_name,
                            original=m.group(0),
                            replacement=replacement if isinstance(replacement, str) else "",
                        ))
                        if confidence == "medium":
                            overall_confidence = "medium"
                    if isinstance(replacement, str):
                        adapted = pattern.sub(replacement, adapted)

        # For && and || we need to handle the closing braces
        # The simple approach: after splitting on the chain operators and
        # replacing, we need to add } after each if-block segment.
        # We handle this by doing a full reconstruction pass.

        # Re-do chain operators with proper wrapping
        adapted, chain_transforms = self._adapt_chain_operators(adapted, command)
        if chain_transforms:
            transforms.extend(chain_transforms)

        if not transforms:
            return AdaptationResult(
                original=command,
                adapted=command,
                shell_target="passthrough",
                confidence="high",
            )

        return AdaptationResult(
            original=command,
            adapted=adapted,
            transforms=transforms,
            confidence=overall_confidence,
            shell_target="powershell",
        )

    def _adapt_chain_operators(
        self, current_adapted: str, original: str
    ) -> tuple[str, list[TransformRecord]]:
        """Handle && and || by wrapping the right-hand side in if($?) blocks.

        Strategy: split the command on && and ||, wrap each right-hand segment,
        then rejoin. This replaces the simple regex substitution above.
        """
        # Check if there are any chain operators in the original command
        # We look for && or || that are NOT inside quotes
        chain_pattern = re.compile(r"(?<!=)(&&|\|\|)")

        parts = chain_pattern.split(current_adapted)
        if len(parts) <= 1:
            adapted_segment = self._adapt_powershell_segment(current_adapted.strip())
            if adapted_segment == current_adapted:
                return current_adapted, []
            return adapted_segment, [TransformRecord(
                pattern="powershell_segment",
                original=current_adapted,
                replacement=adapted_segment,
            )]

        # parts[0] = first command, parts[1] = operator, parts[2] = second command, ...
        # Reconstruct: cmd1 ; if ($?) { cmd2 } ; if (-not $?) { cmd3 } ...
        result_parts = [self._adapt_powershell_segment(parts[0].strip())]
        transforms: list[TransformRecord] = []

        i = 1
        while i < len(parts) - 1:
            op = parts[i]
            cmd = self._adapt_powershell_segment(parts[i + 1].strip())
            if op == "&&":
                result_parts.append(f"; if ($?) {{ {cmd} }}")
                transforms.append(TransformRecord(
                    pattern="chain_and",
                    original=f"{op} {cmd}",
                    replacement=f"; if ($?) {{ {cmd} }}",
                ))
            elif op == "||":
                result_parts.append(f"; if (-not $?) {{ {cmd} }}")
                transforms.append(TransformRecord(
                    pattern="chain_or",
                    original=f"{op} {cmd}",
                    replacement=f"; if (-not $?) {{ {cmd} }}",
                ))
            i += 2

        return " ".join(result_parts), transforms

    @staticmethod
    def _adapt_powershell_segment(segment: str) -> str:
        if segment.lstrip().startswith("&"):
            return segment
        cd_match = re.match(r"""^(\s*)cd\s+/d\s+(.+?)\s*$""", segment, re.IGNORECASE)
        if cd_match:
            return f"{cd_match.group(1)}Set-Location -LiteralPath {cd_match.group(2).strip()}"
        listing = CommandCompatAdapter._adapt_multi_path_listing(segment)
        if listing is not None:
            return listing
        match = re.match(r"""^(\s*)(["'])([^"']+\.(?:exe|cmd|bat|ps1))\2(\s+.*)?$""", segment, re.IGNORECASE)
        if not match:
            return segment
        return f"{match.group(1)}& {segment[len(match.group(1)):]}"

    @staticmethod
    def _adapt_multi_path_listing(segment: str) -> str | None:
        match = re.match(r"""^(\s*)(ls|dir)\s+(.+?)\s*$""", segment, re.IGNORECASE)
        if not match:
            return None
        args_text = match.group(3).strip()
        if "|" in args_text or any(token in args_text for token in (";", "&&", "||")):
            return None
        raw_args = re.findall(r""""[^"]+"|'[^']+'|\S+""", args_text)
        path_args: list[str] = []
        for raw in raw_args:
            token = raw.strip()
            if token in {"2>&1", "1>&2"} or re.match(r"^[12]?>", token):
                continue
            if token in {"-l", "-a", "-la", "-al"}:
                continue
            path_args.append(token)
        if len(path_args) < 2:
            return None
        values: list[str] = []
        for raw in path_args:
            value = raw.strip().rstrip(",")
            if not value or value.startswith("-"):
                return None
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if not value:
                return None
            values.append(value)
        literal_paths = ",".join(CommandCompatAdapter._ps_single_quote(value) for value in values)
        return f"{match.group(1)}Get-ChildItem -LiteralPath {literal_paths}"

    @staticmethod
    def _ps_single_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
