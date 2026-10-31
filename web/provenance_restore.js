import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const pngSignature = [137, 80, 78, 71, 13, 10, 26, 10];

export async function hasProvenance(file) {
    const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    if (signature.length !== 8 || signature.some((byte, index) => byte !== pngSignature[index])) {
        return false;
    }
    let offset = 8;
    while (offset + 8 <= file.size) {
        const header = new DataView(await file.slice(offset, offset + 8).arrayBuffer());
        const length = header.getUint32(0);
        const type = header.getUint32(4);
        if (type === 0x63705256) {
            return true;
        }
        if (type === 0x49454e44) {
            return false;
        }
        offset += length + 12;
    }
    return false;
}

async function fetchResult(route, options) {
    const response = await api.fetchApi(route, options);
    if (!response.ok) {
        const text = await response.text();
        let message = text;
        try {
            message = JSON.parse(text).error ?? text;
        } catch (error) {
            if (!(error instanceof SyntaxError)) {
                throw error;
            }
        }
        throw new Error(`Provenance request failed (${response.status}): ${message}`);
    }
    return response.json();
}

function showFailure(error, summary = "Could not restore PNG provenance") {
    app.extensionManager.toast.add({
        severity: "error", summary, detail: error.message, life: 30000,
    });
}

app.registerExtension({
    name: "ComfyUI.PNGProvenance.Restore",
    setup() {
        let loadQueue = Promise.resolve();
        const loadRestored = (result, name, options, newWorkflow = false) => {
            loadQueue = loadQueue.then(async () => {
                try {
                    if (newWorkflow) {
                        const workflows = app.extensionManager.workflow.workflows;
                        const baseName = name;
                        let suffix = 2;
                        while (workflows.some((workflow) => workflow.fullFilename === `${name}.json`)) {
                            name = `${baseName} (${suffix++})`;
                        }
                    }
                    await app.loadApiJson(result.prompt, name, options);
                    if (result.warnings.length) {
                        app.extensionManager.toast.add({
                            severity: "warn",
                            summary: "Provenance restored; manual image selection required",
                            detail: result.warnings.join("\n"),
                            life: 30000,
                        });
                    }
                } catch (error) {
                    showFailure(error);
                }
            });
            return loadQueue;
        };
        api.addEventListener("provenance.restored", (event) => {
            return loadRestored(event.detail, event.detail.name, undefined, true);
        });

        const registerBrowser = async () => {
            const clientId = api.clientId;
            if (!clientId) {
                return;
            }
            try {
                await fetchResult("/provenance/clients/register", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ client_id: clientId }),
                });
            } catch (error) {
                showFailure(error, "Could not register PNG provenance browser");
            }
        };
        // This handshake also runs on explicit socket replacements without a reconnected event.
        api.addEventListener("feature_flags", registerBrowser);
        registerBrowser();

        const originalHandleFile = app.handleFile;
        app.handleFile = async function (file, openSource, options) {
            if (openSource !== "file_drop") {
                return originalHandleFile.call(this, file, openSource, options);
            }
            try {
                if (!(await hasProvenance(file))) {
                    return originalHandleFile.call(this, file, openSource, options);
                }
                const body = new FormData();
                body.append("image", file);
                const result = await fetchResult("/provenance/restore", { method: "POST", body });
                await loadRestored(result, file.name.replace(/\.png$/i, ""), options);
            } catch (error) {
                showFailure(error);
            }
        };
    },
});
