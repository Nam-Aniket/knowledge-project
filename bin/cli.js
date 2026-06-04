#!/usr/bin/env node
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const pkgDir = path.join(__dirname, '..');
const venvDir = path.join(pkgDir, '.venv');

// 1. Resolve local python binary
const isWin = process.platform === 'win32';
const pythonBin = isWin
  ? path.join(venvDir, 'Scripts', 'python.exe')
  : path.join(venvDir, 'bin', 'python');

const cliScript = path.join(pkgDir, 'cli.py');

if (!fs.existsSync(pythonBin)) {
  console.error('❌ Psyche execution environment is corrupted (virtual environment missing).');
  console.error('Please run: npm rebuild psyche-rag');
  process.exit(1);
}

// 2. Forward arguments and streams to cli.py
const args = [cliScript, ...process.argv.slice(2)];

// Inherit stdio to support interactive prompts (REPL) and standard stream pipes for MCP
const child = spawn(pythonBin, args, {
  stdio: 'inherit',
  env: {
    ...process.env
  }
});

child.on('close', (code) => {
  process.exit(code ?? 0);
});

child.on('error', (err) => {
  console.error('❌ Failed to launch Psyche:', err.message);
  process.exit(1);
});
