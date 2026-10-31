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

app.registerExtension({
    name: "ComfyUI.PNGProvenance.Restore",
    setup() {
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
                const response = await api.fetchApi("/provenance/restore", { method: "POST", body });
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
                    throw new Error(`Provenance restoration failed (${response.status}): ${message}`);
                }
                const result = await response.json();
                await this.loadApiJson(result.prompt, file.name.replace(/\.png$/i, ""), options);
                if (result.warnings.length) {
                    app.extensionManager.toast.add({
                        severity: "warn",
                        summary: "Provenance restored; manual image selection required",
                        detail: result.warnings.join("\n"),
                        life: 30000,
                    });
                }
            } catch (error) {
                app.extensionManager.toast.add({
                    severity: "error",
                    summary: "Could not restore PNG provenance",
                    detail: error.message,
                    life: 30000,
                });
            }
        };
    },
});
