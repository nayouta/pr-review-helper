import os
import subprocess
import json
import re
import ast
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = "your-username"
REPO_NAME = "your-repo"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def fetch_commits_in_pr(pr_number):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/commits"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def fetch_commit_diff(commit_sha):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{commit_sha}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["files"]

def track_add_delete_lines(commits):
    added_lines = {}
    removed_lines = {}

    for commit in commits:
        files = fetch_commit_diff(commit["sha"])
        for file in files:
            filename = file["filename"]
            patch = file.get("patch", "")
            if not patch:
                continue

            for line in patch.splitlines():
                if line.startswith('+') and not line.startswith('+++'):
                    added_lines.setdefault(filename, set()).add(line[1:].strip())
                elif line.startswith('-') and not line.startswith('---'):
                    removed_lines.setdefault(filename, set()).add(line[1:].strip())

    deleted_lines = {}
    for file in added_lines:
        deleted = added_lines[file] & removed_lines.get(file, set())
        if deleted:
            deleted_lines[file] = deleted

    return deleted_lines

def analyze_python_code_ast(source_code, filename="<unknown>"):
    findings = []
    try:
        tree = ast.parse(source_code, filename=filename)

        class Analyzer(ast.NodeVisitor):
            def visit_Call(self, node):
                func_name = self.get_full_name(node.func)
                if func_name in ['print', 'pdb.set_trace', 'logging.debug']:
                    findings.append((node.lineno, func_name, "🐍 Python debug function call"))
                self.generic_visit(node)

            def visit_Constant(self, node):
                if isinstance(node.value, (int, float)):
                    findings.append((node.lineno, str(node.value), "🔢 Use of magic number"))

            def visit_FunctionDef(self, node):
                if len(node.body) > 50:
                    findings.append((node.lineno, node.name, "📏 Function is too long"))
                if self.nesting_depth(node.body) > 4:
                    findings.append((node.lineno, node.name, "🌀 Nesting too deep"))
                self.generic_visit(node)

            def nesting_depth(self, body, level=1):
                max_depth = level
                for stmt in body:
                    if hasattr(stmt, 'body'):
                        depth = self.nesting_depth(stmt.body, level + 1)
                        max_depth = max(max_depth, depth)
                return max_depth

            def get_full_name(self, node):
                if isinstance(node, ast.Name):
                    return node.id
                elif isinstance(node, ast.Attribute):
                    return f"{self.get_full_name(node.value)}.{node.attr}"
                return ""

        Analyzer().visit(tree)

    except Exception as e:
        findings.append((0, f"AST parsing failed: {e}", "⚠️"))

    return findings

def analyze_text_file_lines(file_path, checks):
    findings = []
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        for i, line in enumerate(lines, 1):
            for check in checks:
                if re.search(check["pattern"], line):
                    findings.append((i, line.strip(), check["message"]))
    except Exception as e:
        findings.append((0, str(e), "⚠️ File reading error"))
    return findings

def analyze_ts_code(file_path):
    findings = []
    try:
        result = subprocess.run(['node', 'analyze_ts_ast.js', file_path], capture_output=True, text=True)
        if result.stdout:
            results = json.loads(result.stdout)
            for item in results:
                findings.append((item["line"], item["content"], item["reason"]))
    except Exception as e:
        findings.append((0, str(e), "⚠️ TypeScript parsing failed"))
    return findings

def analyze_terraform(file_path):
    findings = []
    try:
        fmt_result = subprocess.run(['terraform', 'fmt', '-check', file_path], capture_output=True, text=True)
        if fmt_result.returncode != 0:
            findings.append((0, "Terraform fmt check failed", "🧹 Format mismatch"))

        validate_result = subprocess.run(['terraform', 'validate'], cwd=os.path.dirname(file_path),
                                         capture_output=True, text=True)
        if validate_result.returncode != 0:
            findings.append((0, validate_result.stderr.strip(), "🛠️ Terraform syntax error"))
    except Exception as e:
        findings.append((0, str(e), "⚠️ Terraform parsing failed"))
    return findings

def analyze_go_code(file_path):
    findings = []
    try:
        result = subprocess.run(['go', 'vet', file_path], capture_output=True, text=True)
        if result.stderr:
            for line in result.stderr.splitlines():
                findings.append((0, line.strip(), "🧪 go vet warning"))
    except Exception as e:
        findings.append((0, str(e), "⚠️ Go parsing failed"))
    return findings

def analyze_ruby_code(file_path):
    findings = []
    try:
        syntax = subprocess.run(['ruby', '-c', file_path], capture_output=True, text=True)
        if 'Syntax OK' not in syntax.stdout:
            findings.append((0, syntax.stdout.strip(), "🛠️ Ruby syntax error"))

        if subprocess.run(['which', 'rubocop'], capture_output=True).returncode == 0:
            rubo = subprocess.run(['rubocop', '--format', 'simple', file_path], capture_output=True, text=True)
            for line in rubo.stdout.splitlines():
                if ':' in line and 'Offense' in line:
                    findings.append((0, line.strip(), "🧹 RuboCop warning"))
    except Exception as e:
        findings.append((0, str(e), "⚠️ Ruby parsing failed"))
    return findings

def upload_to_gist(content: str, filename: str, token: str) -> str:
    url = "https://api.github.com/gists"
    headers = {"Authorization": f"token {token}"}
    data = {
        "description": f"Review Report: {filename}",
        "public": False,
        "files": {
            filename: {
                "content": content
            }
        }
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 201:
        return response.json()["html_url"]
    else:
        print(f"⚠️ Failed to upload Gist: {response.status_code}, {response.text}")
        return ""

def send_to_discord(message: str, webhook_url: str):
    payload = {
        "embeds": [
            {
                "title": "📋 PR Review Summary",
                "description": message,
                "color": 0x3498db  # Blue color
            }
        ]
    }
    response = requests.post(webhook_url, json=payload)
    if response.status_code == 204:
        print("✅ Sent message to Discord")
    else:
        print(f"⚠️ Failed to send to Discord: {response.status_code} - {response.text}")

def review_pr(pr_number):
    markdown = []
    markdown.append(f"# 📋 Pull Request Review Report - #{pr_number}\n")

    # PR情報の取得
    pr_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}"
    pr_info = requests.get(pr_url, headers=headers).json()
    pr_user = pr_info.get("user", {}).get("login", "unknown")
    pr_created_at = pr_info.get("created_at", "unknown")
    pr_title = pr_info.get("title", "No title")
    pr_changed_files = pr_info.get("changed_files", 0)

    commits = fetch_commits_in_pr(pr_number)
    deleted_lines = track_add_delete_lines(commits)
    debug_findings = []

    for commit in commits:
        files = fetch_commit_diff(commit["sha"])
        for file in files:
            filename = file["filename"]
            patch = file.get("patch", "")
            if not patch:
                continue

            added_code = '\n'.join([
                line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++')
            ])

            file_path = f"/tmp/{os.path.basename(filename)}"
            with open(file_path, "w") as f:
                f.write(added_code)

            if filename.endswith(".py"):
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_python_code_ast(added_code, filename)
                ])
            elif filename.endswith((".ts", ".tsx", ".js", ".jsx")):
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_ts_code(file_path)
                ])
            elif filename.endswith((".cpp", ".hpp", ".java", ".rs", ".html", ".css")):
                checks = []
                if filename.endswith(".cpp"):
                    checks = [
                        {"pattern": r'\b(std::)?cout|printf\(', "message": "🖨️ C++ debug output"},
                        {"pattern": r'[^\w](\d+(\.\d+)?)[^\w]', "message": "🔢 Magic number"}
                    ]
                elif filename.endswith(".java"):
                    checks = [
                        {"pattern": r'System\.out\.println', "message": "🖨️ Java debug output"},
                        {"pattern": r'[^\w](\d+(\.\d+)?)[^\w]', "message": "🔢 Magic number"}
                    ]
                elif filename.endswith(".rs"):
                    checks = [
                        {"pattern": r'dbg!\(|println!\(', "message": "🦀 Rust debug output"}
                    ]
                elif filename.endswith(".html"):
                    checks = [
                        {"pattern": r'<script|<style', "message": "📎 Inline JS/CSS"}
                    ]
                elif filename.endswith(".css"):
                    checks = [
                        {"pattern": r'!important', "message": "🚨 Overuse of !important"}
                    ]
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_text_file_lines(file_path, checks)
                ])
            elif filename.endswith(".tf"):
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_terraform(file_path)
                ])
            elif filename.endswith(".go"):
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_go_code(file_path)
                ])
            elif filename.endswith(".rb"):
                debug_findings.extend([
                    (filename, lineno, code, reason)
                    for lineno, code, reason in analyze_ruby_code(file_path)
                ])

    if deleted_lines:
        markdown.append("## 🔁 Detected Code Deletions\n")
        for file, lines in deleted_lines.items():
            markdown.append(f"### 📄 {file}")
            for line in lines:
                markdown.append(f"- `{line}`")
            markdown.append("")

    if debug_findings:
        markdown.append("## 🐛 Debug Code / Best Practice Violations\n")
        for file, line_num, code, reason in debug_findings:
            markdown.append(f"### 📄 {file} (L{line_num})\n")
            markdown.append(f"- **Type**: {reason}\n")
            ext = file.split('.')[-1]
            markdown.append(f"```{ext}\n{code}\n```\n")
    else:
        markdown.append("✅ No debug code or best practice violations found.\n")

    output_path = f"./report/pr_{pr_number}_review_report.md"
    with open(output_path, "w") as f:
        f.write('\n'.join(markdown))

    print(f"📄 Markdown report written to: {output_path}")

    gist_url = upload_to_gist('\n'.join(markdown), output_path, GITHUB_TOKEN)

    summary = (
        f":memo: **PR Review Summary - #{pr_number}**\n"
        f"• 📝 *Title*: **{pr_title}**\n"
        f"• 👤 *Author*: **{pr_user}**\n"
        f"• 📅 *Created At*: **{pr_created_at}**\n"
        f"• 🗂 *Changed Files*: **{pr_changed_files}**\n"
        f"• 🔁 *Code Deletions*: **{len(deleted_lines)}**\n"
        f"• 🐛 *Violations Found*: **{len(debug_findings)}**\n"
        f"• 📄 *Full Report*: {gist_url if gist_url else '(upload failed)'}"
    )
    send_to_discord(summary, DISCORD_WEBHOOK_URL)

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python pr_review_tool.py <PR_NUMBER>")
        sys.exit(1)

    pr_number = int(sys.argv[1])
    review_pr(pr_number)
