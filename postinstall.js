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

  // Cursor commands
  const cursorCommandsDir = path.join(home, '.cursor', 'commands');
  if (!fs.existsSync(cursorCommandsDir)) {
    fs.mkdirSync(cursorCommandsDir, { recursive: true });
  }
  fs.writeFileSync(path.join(cursorCommandsDir, 'psyche.md'), promptContent, 'utf8');
  console.log('✅ Registered Cursor slash command prompt.');

  // 6. Register MCP configuration in Codex, Gemini/Antigravity, and Claude Desktop
  try {
    const cliPath = path.join(pkgDir, 'bin', 'cli.js');
    const nodeBin = process.execPath;
    
    const mcpConfig = {
      command: nodeBin,
      args: [cliPath, 'start-mcp']
    };

    // Helper to update TOML block in ~/.codex/config.toml
    function updateTomlBlock(content, sectionName, newBlockObj) {
      const lines = content.split(/\r?\n/);
      let sectionIndex = -1;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim() === `[${sectionName}]`) {
          sectionIndex = i;
          break;
        }
      }

      const blockLines = [];
      blockLines.push(`[${sectionName}]`);
      for (const [key, val] of Object.entries(newBlockObj)) {
        if (typeof val === 'string') {
          blockLines.push(`${key} = "${val}"`);
        } else if (Array.isArray(val)) {
          blockLines.push(`${key} = ${JSON.stringify(val)}`);
        } else if (typeof val === 'number' || typeof val === 'boolean') {
          blockLines.push(`${key} = ${val}`);
        }
      }

      if (sectionIndex !== -1) {
        let endIndex = lines.length;
        for (let i = sectionIndex + 1; i < lines.length; i++) {
          if (lines[i].trim().startsWith('[')) {
            endIndex = i;
            break;
          }
        }
        lines.splice(sectionIndex, endIndex - sectionIndex, ...blockLines);
      } else {
        if (lines.length > 0 && lines[lines.length - 1].trim() !== '') {
          lines.push('');
        }
        lines.push(...blockLines);
      }
      return lines.join('\n');
    }

    // Helper to update JSON configurations
    function updateJsonConfig(filePath, mcpServerName, mcpConfig) {
      let config = {};
      if (fs.existsSync(filePath)) {
        try {
          const content = fs.readFileSync(filePath, 'utf8');
          config = JSON.parse(content);
        } catch (e) {
          console.warn(`⚠️ Warning: Could not parse JSON in ${filePath}, starting fresh.`, e.message);
        }
      }

      if (!config.mcpServers) {
        config.mcpServers = {};
      }
      config.mcpServers[mcpServerName] = mcpConfig;

      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify(config, null, 2), 'utf8');
    }

    // A. Codex (~/.codex/config.toml)
    const codexConfigPath = path.join(home, '.codex', 'config.toml');
    let tomlContent = '';
    if (fs.existsSync(codexConfigPath)) {
      tomlContent = fs.readFileSync(codexConfigPath, 'utf8');
    } else {
      fs.mkdirSync(path.dirname(codexConfigPath), { recursive: true });
    }
    const updatedToml = updateTomlBlock(tomlContent, 'mcp_servers.psyche', mcpConfig);
    fs.writeFileSync(codexConfigPath, updatedToml, 'utf8');
    console.log('✅ Registered Psyche MCP server in Codex config.');

    // B. Gemini/Antigravity, Cursor, and Windsurf
    const mcpJsonConfigs = [
      { name: 'Gemini (Antigravity)', path: path.join(home, '.gemini', 'antigravity', 'mcp_config.json') },
      { name: 'Gemini (Antigravity-IDE)', path: path.join(home, '.gemini', 'antigravity-ide', 'mcp_config.json') },
      { name: 'Cursor', path: path.join(home, '.cursor', 'mcp.json') },
      { name: 'Windsurf', path: path.join(home, '.codeium', 'windsurf', 'mcp_config.json') }
    ];
    for (const item of mcpJsonConfigs) {
      try {
        updateJsonConfig(item.path, 'psyche', mcpConfig);
        console.log(`✅ Registered Psyche MCP server in ${item.name} config: ${item.path}`);
      } catch (e) {
        console.warn(`⚠️ Warning: Could not register in ${item.name} config:`, e.message);
      }
    }

    // C. Claude Desktop
    let claudeConfigPath;
    if (process.platform === 'darwin') {
      claudeConfigPath = path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json');
    } else if (process.platform === 'win32') {
      claudeConfigPath = path.join(process.env.APPDATA || '', 'Claude', 'claude_desktop_config.json');
    } else {
      claudeConfigPath = path.join(home, '.config', 'Claude', 'claude_desktop_config.json');
    }

    updateJsonConfig(claudeConfigPath, 'psyche', mcpConfig);
    console.log(`✅ Registered Psyche MCP server in Claude Desktop config: ${claudeConfigPath}`);

  } catch (mcpErr) {
    console.warn('⚠️ Warning: Could not register MCP server configuration:', mcpErr.message);
  }
} catch (err) {
  console.warn('⚠️ Warning: Could not register slash command prompts:', err.message);
}
