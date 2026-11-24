# Clicker RAG

Clicker helps job applicants turn resumes, project reports, and past applications into Korean cover-letter paragraphs. Import a file or folder, then enter the company, role, application question, and what you want to emphasize.

For example, ask Clicker to describe how you integrated two systems. It extracts tags from your instructions, searches your material by tag and meaning in PostgreSQL with pgvector, and sends the selected excerpts to an Ollama writing model. You can open the sources, edit the paragraph, and apply it to your draft. When you write the next paragraph, Clicker uses the preceding draft text for continuity. The web interface is in Korean.

## Installation

1. **Prepare a 64-bit Windows machine.** Install Python 3.11 with the Windows Python Launcher, Node.js with npm, and Git. The first setup uses an internet connection. Open PowerShell and check that the tools are available:

   ```powershell
   py -3.11 --version
   npm.cmd --version
   git --version
   winget --version
   ```

2. **Get the project.**

   ```powershell
   git clone https://github.com/levicorp-us/clicker-rag.git
   cd clicker-rag
   ```

3. **Choose where models will run.** For local inference, continue to step 4. The launcher installs Ollama with WinGet if needed. To use an existing Ollama server instead, set its address in the same PowerShell window before starting Clicker. Replace the example hostname with your own:

   ```powershell
   $env:CLICKER_OLLAMA_URL = "http://your-model-server:11434"
   ```

4. **Install and start Clicker.** From the project folder, run:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Setup -OpenBrowser
   ```

   This creates a Python environment, installs PostgreSQL 17 and pgvector, and starts the web app and background worker. When you see **Clicker ready**, the browser opens the [writing workspace](http://127.0.0.1:8765/).

5. **Install and select models.** Open [Model Settings](http://127.0.0.1:8765/#models). Pick installed models or download them by their Ollama registry names. For example, use `qwen3:8b` for writing and tag extraction and `bge-m3` for retrieval. You can also register a single GGUF file. Select the models for their roles and click the button beneath the selectors to validate and apply them. Wait for the settings page to show the new active models. Model weights are downloaded separately on the Ollama server.

6. **Start and stop on later visits.** Run either command from the project folder. If you use a remote model server, set `CLICKER_OLLAMA_URL` again in each new PowerShell session before starting.

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -OpenBrowser
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Stop
   ```

   Stopping the app preserves your work. Keep the project’s `.runtime` folder and the database under `%LOCALAPPDATA%\ClickerRag` between runs.
