import os
import sys
import subprocess
import shutil

def register_mcp_configs(python_bin, project_root):
    import json
    
    # We want absolute paths
    abs_python_bin = os.path.abspath(python_bin)
    abs_cli_py = os.path.join(os.path.abspath(project_root), "cli.py")
    
    mcp_config = {
        "command": abs_python_bin,
        "args": ["-u", abs_cli_py, "start-mcp"]
    }
    
    home = os.path.expanduser("~")
    
    # Helper to update TOML block in ~/.codex/config.toml
    def update_toml_block(content, section_name, new_block_dict):
        lines = content.splitlines()
        section_index = -1
        for i, line in enumerate(lines):
            if line.strip() == f"[{section_name}]":
                section_index = i
                break
                
        block_lines = [f"[{section_name}]"]
        for k, v in new_block_dict.items():
            if isinstance(v, str):
                block_lines.append(f'{k} = "{v}"')
            elif isinstance(v, (list, tuple)):
                block_lines.append(f'{k} = {json.dumps(v)}')
            elif isinstance(v, bool):
                block_lines.append(f'{k} = {"true" if v else "false"}')
            elif isinstance(v, (int, float)):
                block_lines.append(f'{k} = {v}')
                
        if section_index != -1:
            end_index = len(lines)
            for i in range(section_index + 1, len(lines)):
                if lines[i].strip().startswith('['):
                    end_index = i
                    break
            lines[section_index:end_index] = block_lines
        else:
            if lines and lines[-1].strip() != '':
                lines.append('')
            lines.extend(block_lines)
            
        return '\n'.join(lines) + '\n'

    # Helper to update JSON configurations
    def update_json_config(file_path, mcp_server_name, mcp_config_dict):
        config = {}
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception as e:
                print(f"⚠️ Warning: Could not parse JSON in {file_path}: {e}")
                
        if "mcpServers" not in config:
            config["mcpServers"] = {}
            
        config["mcpServers"][mcp_server_name] = mcp_config_dict
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"⚠️ Warning: Could not write JSON to {file_path}: {e}")

    print("\nRegistering Psyche MCP Server in configurations...")
    
    # A. Codex (~/.codex/config.toml)
    try:
        codex_config_path = os.path.join(home, ".codex", "config.toml")
        toml_content = ""
        if os.path.exists(codex_config_path):
            try:
                with open(codex_config_path, 'r', encoding='utf-8') as f:
                    toml_content = f.read()
            except Exception:
                pass
        else:
            os.makedirs(os.path.dirname(codex_config_path), exist_ok=True)
            
        updated_toml = update_toml_block(toml_content, "mcp_servers.psyche", mcp_config)
        with open(codex_config_path, 'w', encoding='utf-8') as f:
            f.write(updated_toml)
        print("✅ Registered Psyche MCP server in Codex config.")
    except Exception as e:
        print(f"⚠️ Warning: Could not register in Codex config: {e}")

    # B. Gemini/Antigravity, Cursor, and Windsurf
    mcp_json_configs = [
        {"name": "Gemini (Antigravity)", "path": os.path.join(home, ".gemini", "antigravity", "mcp_config.json")},
        {"name": "Gemini (Antigravity-IDE)", "path": os.path.join(home, ".gemini", "antigravity-ide", "mcp_config.json")},
        {"name": "Cursor", "path": os.path.join(home, ".cursor", "mcp.json")},
        {"name": "Windsurf", "path": os.path.join(home, ".codeium", "windsurf", "mcp_config.json")}
    ]
    for item in mcp_json_configs:
        try:
            update_json_config(item["path"], "psyche", mcp_config)
            print(f"✅ Registered Psyche MCP server in {item['name']} config: {item['path']}")
        except Exception as e:
            print(f"⚠️ Warning: Could not register in {item['name']} config: {e}")

    # C. Claude Desktop
    try:
        if sys.platform == "darwin":
            claude_config_path = os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
        elif sys.platform == "win32":
            claude_config_path = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
        else:
            claude_config_path = os.path.join(home, ".config", "Claude", "claude_desktop_config.json")
            
        update_json_config(claude_config_path, "psyche", mcp_config)
        print(f"✅ Registered Psyche MCP server in Claude Desktop config: {claude_config_path}")
    except Exception as e:
        print(f"⚠️ Warning: Could not register in Claude Desktop config: {e}")

def register_slash_prompts(project_root):
    home = os.path.expanduser("~")
    prompt_content = """---
description: Query the Psyche database for your books and notes
argument-hint: [query]
---
Search the psyche database for: "$ARGUMENTS"
"""
    
    # A. Codex prompts
    try:
        codex_prompts_dir = os.path.join(home, ".codex", "prompts")
        os.makedirs(codex_prompts_dir, exist_ok=True)
        with open(os.path.join(codex_prompts_dir, "psyche.md"), 'w', encoding='utf-8') as f:
            f.write(prompt_content)
        print("✅ Registered Codex slash command prompt.")
    except Exception as e:
        print(f"⚠️ Warning: Could not register Codex slash command prompt: {e}")

    # B. Gemini commands
    try:
        gemini_commands_dir = os.path.join(home, ".gemini", "commands")
        os.makedirs(gemini_commands_dir, exist_ok=True)
        with open(os.path.join(gemini_commands_dir, "psyche.md"), 'w', encoding='utf-8') as f:
            f.write(prompt_content)
            
        gemini_toml_content = """description = "Query the Psyche database for your books and notes"
prompt = \"\"\"
Search the psyche database for: "$ARGUMENTS"
\"\"\"
"""
        with open(os.path.join(gemini_commands_dir, "psyche.toml"), 'w', encoding='utf-8') as f:
            f.write(gemini_toml_content)
        print("✅ Registered Gemini/Antigravity slash command prompt.")
    except Exception as e:
        print(f"⚠️ Warning: Could not register Gemini/Antigravity slash command prompt: {e}")

    # C. Cursor commands
    try:
        cursor_commands_dir = os.path.join(home, ".cursor", "commands")
        os.makedirs(cursor_commands_dir, exist_ok=True)
        with open(os.path.join(cursor_commands_dir, "psyche.md"), 'w', encoding='utf-8') as f:
            f.write(prompt_content)
        print("✅ Registered Cursor slash command prompt.")
    except Exception as e:
        print(f"⚠️ Warning: Could not register Cursor slash command prompt: {e}")

def run_setup():
    # If PSYCHE_SETUP_WIZARD_ONLY is set, we just run the interactive wizard
    if os.environ.get("PSYCHE_SETUP_WIZARD_ONLY") == "true":
        run_wizard_phase()
        return

    print("🧠 Setting up Psyche RAG Engine...")

    # 1. Initialize Virtual Environment
    venv_dir = ".venv"
    if not os.path.isdir(venv_dir):
        print("Creating virtual environment in .venv...")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)

    # Determine binary and pip paths
    if sys.platform == "win32":
        pip_path = os.path.join(venv_dir, "Scripts", "pip.exe")
        psyche_bin = os.path.join(venv_dir, "Scripts", "psyche.exe")
        python_bin = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        pip_path = os.path.join(venv_dir, "bin", "pip")
        psyche_bin = os.path.join(venv_dir, "bin", "psyche")
        python_bin = os.path.join(venv_dir, "bin", "python")

    # 2. Install Package & Dependencies
    print("Installing package and dependencies in editable mode...")
    subprocess.run([pip_path, "install", "-e", "."], check=True)

    # 3. Create global symlink (macOS/Linux only)
    if sys.platform != "win32":
        print("Registering global 'psyche' command...")
        linked = False
        # Try /opt/homebrew/bin, /usr/local/bin, ~/.local/bin
        global_dirs = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]
        abs_psyche_bin = os.path.abspath(psyche_bin)
        
        for g_dir in global_dirs:
            if os.path.isdir(g_dir):
                dst = os.path.join(g_dir, "psyche")
                try:
                    if os.path.exists(dst) or os.path.islink(dst):
                        os.remove(dst)
                    os.symlink(abs_psyche_bin, dst)
                    print(f"✅ Success! 'psyche' command linked to {dst}")
                    linked = True
                    break
                except Exception:
                    # Continue to next directory if this one fails (e.g. permission error)
                    continue
        
        if not linked:
            # If we couldn't write to any standard dirs, try to create ~/.local/bin
            local_bin = os.path.expanduser("~/.local/bin")
            try:
                os.makedirs(local_bin, exist_ok=True)
                dst = os.path.join(local_bin, "psyche")
                if os.path.exists(dst) or os.path.islink(dst):
                    os.remove(dst)
                os.symlink(abs_psyche_bin, dst)
                print(f"✅ Success! 'psyche' command linked to {dst}")
                linked = True
            except Exception as e:
                print(f"⚠️  Could not create symlink at {dst}: {e}")
                print(f"You can run psyche using: {abs_psyche_bin}")

    # 3.5 Register MCP configuration and slash prompts
    project_root_dir = os.path.dirname(os.path.abspath(__file__))
    register_mcp_configs(python_bin, project_root_dir)
    register_slash_prompts(project_root_dir)

    # 4. Run setup wizard using the virtualenv python to avoid ModuleNotFound errors
    print("\nLaunching Interactive Setup Wizard...")
    os.environ["PSYCHE_SETUP_WIZARD_ONLY"] = "true"
    # Pass along existing environment
    env = os.environ.copy()
    
    # We run 'setup' subcommand via virtual env python
    subprocess.run([python_bin, "cli.py", "setup"], env=env, check=True)

def run_wizard_phase():
    # Now we are running inside the virtualenv python, so dependencies like rich are available!
    # Ensure project root is in sys.path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.append(project_root)
        
    from llm_client import run_setup_wizard
    env_path = os.path.join(project_root, ".env")
    run_setup_wizard(env_path)
