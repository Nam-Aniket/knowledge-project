#!/usr/bin/env python3
import os
import sys
import subprocess

# Ensure project root is in path so we can import llm_client
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from llm_client import LLMClient

def main():
    # 1. Fetch Git Diff and Commit Message
    try:
        commit_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project_root).decode("utf-8").strip()
        commit_msg = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], cwd=project_root).decode("utf-8").strip()
        
        # Check if this is the first commit or if we can run diff
        try:
            diff = subprocess.check_output(["git", "diff", "HEAD~1", "HEAD"], cwd=project_root).decode("utf-8").strip()
        except subprocess.CalledProcessError:
            # Fallback if HEAD~1 doesn't exist (initial commit)
            diff = subprocess.check_output(["git", "diff", "--cached"], cwd=project_root).decode("utf-8").strip()
    except Exception as e:
        # Not a git repository or git not available
        sys.stderr.write(f"[Psyche Git Logger] Error reading git info: {e}\n")
        sys.exit(0)

    if not diff:
        sys.stderr.write("[Psyche Git Logger] No changes found in commit. Skipping log generation.\n")
        sys.exit(0)

    # Truncate diff if it is too large
    max_char_limit = 20000
    if len(diff) > max_char_limit:
        diff = diff[:max_char_limit] + "\n\n... [Diff truncated due to size] ..."

    # 2. Initialize LLM Client
    try:
        llm = LLMClient()
        if llm.provider == "none" or llm.chat_model == "none":
            sys.stderr.write("[Psyche Git Logger] Offline mode or no chat model configured. Skipping.\n")
            sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"[Psyche Git Logger] Error initializing LLM: {e}\n")
        sys.exit(0)

    # 3. Request LLM Summary
    system_instruction = (
        "You are an automated software architecture archivist. Analyze the provided Git diff "
        "and commit message of the user's latest code changes. Write a concise, 3-section "
        "summary (markdown format) that explains the problem solved, the key code changes, "
        "and any takeaway rules of thumb for future developer agents working on this codebase."
    )
    
    prompt = (
        f"Commit Hash: {commit_hash}\n"
        f"Commit Message: {commit_msg}\n\n"
        f"Diff:\n{diff}"
    )

    sys.stdout.write("[Psyche Git Logger] Generating automated learning log using LLM...\n")
    try:
        log_content = llm.generate_completion(system_instruction, prompt)
    except Exception as e:
        sys.stderr.write(f"[Psyche Git Logger] API call failed: {e}\n")
        sys.exit(0)

    # 4. Resolve log destination path
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, ".env"))
    watch_path = os.getenv("WATCH_PATH")
    if not watch_path:
        watch_path = os.path.expanduser("~/.psyche/logs")
    
    os.makedirs(watch_path, exist_ok=True)
    log_file_name = f"commit_{commit_hash[:8]}.md"
    log_file_path = os.path.join(watch_path, log_file_name)

    # 5. Write Markdown Log File
    try:
        # Extract first line of commit message for the title
        title = commit_msg.splitlines()[0] if commit_msg else "Automatic Git Log"
        with open(log_file_path, "w", encoding="utf-8") as f:
            f.write(f"# [Git Commit Log] {title}\n")
            f.write(f"- **Commit Hash**: {commit_hash}\n")
            f.write(f"- **Author**: Git Automation\n\n")
            f.write(log_content)
            f.write("\n")
        sys.stdout.write(f"[Psyche Git Logger] Successfully generated learning log at: {log_file_path}\n")
    except Exception as e:
        sys.stderr.write(f"[Psyche Git Logger] Failed to write log file: {e}\n")

if __name__ == "__main__":
    main()
