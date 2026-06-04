import os
import sys
import subprocess
import shutil

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
