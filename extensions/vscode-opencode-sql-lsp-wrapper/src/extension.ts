import * as vscode from "vscode";
import {
  Executable,
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

function buildExecutable(): Executable {
  const configuration = vscode.workspace.getConfiguration("opencodeSql");
  const command = configuration.get<string>("serverPath", "opencode-sql-lsp");
  const args = configuration.get<string[]>("serverArgs", ["--stdio"]);

  return {
    command,
    args,
    transport: TransportKind.stdio,
  };
}

async function showLaunchFailure(error: unknown): Promise<void> {
  const message = error instanceof Error ? error.message : String(error);
  await vscode.window.showErrorMessage(
    `OpenCode SQL LSP failed to start. Ensure 'opencode-sql-lsp' is installed and available on PATH, or configure 'opencodeSql.serverPath'. Details: ${message}`
  );
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const serverOptions: ServerOptions = buildExecutable();
  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ scheme: "file", language: "sql" }],
  };

  client = new LanguageClient(
    "opencodeSql",
    "OpenCode SQL LSP",
    serverOptions,
    clientOptions
  );

  try {
    await client.start();
    context.subscriptions.push({ dispose: () => void deactivate() });
  } catch (error) {
    await showLaunchFailure(error);
    throw error;
  }
}

export async function deactivate(): Promise<void> {
  if (client === undefined) {
    return;
  }

  const activeClient = client;
  client = undefined;
  await activeClient.stop();
}
