# Clicker RAG

Clicker helps job applicants turn resumes, project reports, and past applications into Korean cover-letter paragraphs. Import a file or folder, then enter the company, role, application question, and what you want to emphasize.

For example, ask Clicker to describe how you integrated two systems. It extracts tags from your instructions, searches your material by tag and meaning in PostgreSQL with pgvector, and sends the selected excerpts to an Ollama writing model. You can open the sources, edit the paragraph, and apply it to your draft. When you write the next paragraph, Clicker uses the preceding draft text for continuity. The web interface is in Korean.

## Install on Windows

Use an x64 Windows PC with internet access and WinGet (Microsoft App Installer). The three model downloads total about 20 GiB on the computer running Ollama. Allow extra space for the application, database, and your documents.

1. **Download and extract Clicker.** [Download the project ZIP](https://github.com/levicorp-us/clicker-rag/archive/refs/heads/main.zip). In File Explorer, right-click the ZIP, choose **Extract All**, then open the extracted folder containing `install.ps1`. Choose a location you can keep between sessions.

2. **Open PowerShell in that folder.** Click the File Explorer address bar, type `powershell`, and press Enter. For a normal local installation, go straight to step 3. If you already run Ollama on another machine, set its address first, replacing the example hostname. The installer will fetch the models on that server.

   ```powershell
   $env:CLICKER_OLLAMA_URL = "http://your-model-server:11434"
   ```

3. **Run the installer.** It installs missing prerequisites (Python 3.11, Node.js with npm, and local Ollama when needed), sets up PostgreSQL with pgvector, and installs the models. Windows may ask for permission to install a prerequisite. Keep PowerShell open until you see **Clicker ready**. If a download is interrupted, run the same command again in the same folder.

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

4. **Check the first launch.** The installer opens the [writing workspace](http://127.0.0.1:8765/). Open [Model Settings](http://127.0.0.1:8765/#models) to see the installed models and the active selection for each role.

   | Installed model | Use on a new installation |
   | --- | --- |
   | `qwen3.8:27b` | Default for paragraph writing and tag extraction. On a local RTX 2060 with 6 GB VRAM, Clicker creates and selects the text profile `clicker-qwen3.8:27b`. |
   | `bge-m3` | Default embedding model for semantic search. |
   | `qwen3:4b` | Smaller alternative for writing or tag extraction. Select it in Model Settings and apply the change to use it. |

5. **Try a first paragraph.** Import a short project report under your materials and wait for its analysis to finish in the task panel. Open the writing workspace, enter a company, role, and application question, then give the material-based writer an instruction such as “Describe how I integrated two systems.” Review the paragraph and its cited material before adding it to your draft.

## Start and stop later

For a local installation, double-click **start.cmd** to open Clicker or **stop.cmd** to shut it down. Your documents and drafts persist between runs. Keep both the project's `.runtime` folder and the database in `%LOCALAPPDATA%\ClickerRag`.

For a remote Ollama server, set `CLICKER_OLLAMA_URL` as in step 2 in each new PowerShell session, then run `.\start.cmd` from that window.

If setup reports that WinGet is missing, install or update [Microsoft App Installer](https://apps.microsoft.com/detail/9nblggh4nns1), reopen PowerShell, and repeat step 3.
