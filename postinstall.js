#!/usr/bin/env node
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const pkgDir = __dirname;
const venvDir = path.join(pkgDir, '.venv');

console.log('🧠 Psyche post-install: Setting up local Python environment...');

// 1. Verify Python 3 is installed
let pythonCmd = 'python3';
try {
  execSync('python3 --version', { stdio: 'ignore' });
} catch (e) {
  try {
    execSync('python --version', { stdio: 'ignore' });
    pythonCmd = 'python';
  } catch (err) {
    console.error('❌ Error: Python 3 was not found on your system.');
    console.error('Psyche requires Python 3 to run its GraphRAG engine.');
    process.exit(1);
  }
}

// 2. Create local .venv virtualenv
if (!fs.existsSync(venvDir)) {
  console.log(`Creating virtual environment in: ${venvDir}`);
  try {
    execSync(`"${pythonCmd}" -m venv "${venvDir}"`, { stdio: 'inherit' });
  } catch (err) {
    console.error('❌ Failed to create Python virtual environment:', err.message);
    process.exit(1);
  }
}

// 3. Resolve pip path
const isWin = process.platform === 'win32';
const pipPath = isWin
  ? path.join(venvDir, 'Scripts', 'pip.exe')
  : path.join(venvDir, 'bin', 'pip');

// 4. Install dependencies in editable/local mode
console.log('Installing Python package dependencies...');
try {
  execSync(`"${pipPath}" install -e "${pkgDir}"`, { stdio: 'inherit', cwd: pkgDir });
  console.log('✅ Local Python environment successfully configured.');
} catch (err) {
  console.error('❌ Failed to install Python dependencies:', err.message);
  process.exit(1);
}

// 5. Register custom prompt slash command in Codex and Gemini directories
try {
  const os = require('os');
  const home = os.homedir();
  
  // Codex prompts
  const codexPromptsDir = path.join(home, '.codex', 'prompts');
  if (!fs.existsSync(codexPromptsDir)) {
    fs.mkdirSync(codexPromptsDir, { recursive: true });
  }
  const promptContent = `---
description: Query the Psyche database for your books and notes
argument-hint: [query]
---
Search the psyche database for: "$ARGUMENTS"
`;
  fs.writeFileSync(path.join(codexPromptsDir, 'psyche.md'), promptContent, 'utf8');
  console.log('✅ Registered Codex slash command prompt.');

  // Gemini commands
  const geminiCommandsDir = path.join(home, '.gemini', 'commands');
  if (!fs.existsSync(geminiCommandsDir)) {
    fs.mkdirSync(geminiCommandsDir, { recursive: true });
  }
  fs.writeFileSync(path.join(geminiCommandsDir, 'psyche.md'), promptContent, 'utf8');
  
  const geminiTomlContent = `description = "Query the Psyche database for your books and notes"
prompt = """
Search the psyche database for: "$ARGUMENTS"
"""
`;
  fs.writeFileSync(path.join(geminiCommandsDir, 'psyche.toml'), geminiTomlContent, 'utf8');
  console.log('✅ Registered Gemini/Antigravity slash command prompt.');
} catch (err) {
  console.warn('⚠️ Warning: Could not register slash command prompts:', err.message);
}
