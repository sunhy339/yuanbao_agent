import { memo, useMemo, useState } from "react";
import { Copy, FileText, Image as ImageIcon, X } from "lucide-react";

const imageExtensionPattern = /\.(png|jpe?g|gif|webp|svg|bmp|avif|ico)$/i;

export type CleanAttachment = {
  id: string;
  type: "image" | "file";
  name: string;
  path: string;
  src?: string;
  note?: string;
};

function normalizePath(value: string) {
  return value.trim().replace(/^["'`]+|["'`,;:]+$/g, "").replace(/\\/g, "/");
}

function basename(value: string) {
  const normalized = normalizePath(value);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "attachment";
}

function isImagePath(value: string) {
  return imageExtensionPattern.test(value);
}

function isSafeImageSrc(value: string) {
  return /^(https?:|data:image\/|blob:|file:)/i.test(value) || value.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(value);
}

function readAttachmentRecord(value: unknown): CleanAttachment | null {
  if (typeof value === "string") {
    const path = normalizePath(value);
    if (!path) return null;
    const type = isImagePath(path) ? "image" : "file";
    return {
      id: path,
      type,
      name: basename(path),
      path,
      src: type === "image" && isSafeImageSrc(path) ? path : undefined,
    };
  }
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const rawPath = [record.previewUrl, record.url, record.src, record.path, record.file, record.filePath, record.name]
    .find((entry) => typeof entry === "string" && entry.trim());
  if (typeof rawPath !== "string") return null;
  const path = normalizePath(rawPath);
  const explicitType = typeof record.type === "string" ? record.type.toLowerCase() : "";
  const type = explicitType.includes("image") || isImagePath(path) ? "image" : "file";
  const name = typeof record.name === "string" && record.name.trim() ? record.name.trim() : basename(path);
  const note = typeof record.note === "string" ? record.note.trim() : typeof record.quote === "string" ? record.quote.trim() : "";
  return {
    id: String(record.id ?? path),
    type,
    name,
    path,
    src: type === "image" && isSafeImageSrc(path) ? path : undefined,
    note: note || undefined,
  };
}

export function attachmentsFromMetadata(metadata?: Record<string, unknown>) {
  if (!metadata) return [];
  const keys = ["attachments", "images", "imagePaths", "files", "artifacts"];
  const attachments: CleanAttachment[] = [];
  keys.forEach((key) => {
    const value = metadata[key];
    const values = Array.isArray(value) ? value : value ? [value] : [];
    values.forEach((entry) => {
      const attachment = readAttachmentRecord(entry);
      if (attachment) attachments.push(attachment);
    });
  });
  return uniqueAttachments(attachments);
}

export function imageAttachmentsFromText(text: string) {
  const attachments: CleanAttachment[] = [];
  const seen = new Set<string>();
  const markdownImages = new Set(
    Array.from(text.matchAll(/!\[[^\]]*]\(([^)]+)\)/g))
      .map((match) => normalizePath(match[1] ?? ""))
      .filter(Boolean),
  );
  const pattern = /(?:^|[\s`"'(:：])((?:[A-Za-z]:[\\/]|\/|https?:\/\/|file:\/\/)[^\s`"')<>]+\.(?:png|jpe?g|gif|webp|svg|bmp|avif|ico))/gim;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const path = normalizePath(match[1] ?? "");
    if (!path || seen.has(path) || markdownImages.has(path)) continue;
    seen.add(path);
    attachments.push({
      id: path,
      type: "image",
      name: basename(path),
      path,
      src: isSafeImageSrc(path) ? path : undefined,
    });
  }
  return attachments;
}

export function uniqueAttachments(attachments: CleanAttachment[]) {
  const byPath = new Map<string, CleanAttachment>();
  attachments.forEach((attachment) => {
    const key = normalizePath(attachment.path || attachment.name);
    if (!key || byPath.has(key)) return;
    byPath.set(key, { ...attachment, path: key });
  });
  return Array.from(byPath.values());
}

export const CleanAttachmentGallery = memo(function CleanAttachmentGallery({
  attachments,
  onCopy,
}: {
  attachments: CleanAttachment[];
  onCopy?: (label: string, text: string) => void | Promise<void>;
}) {
  const [activeImage, setActiveImage] = useState<CleanAttachment | null>(null);
  const visible = useMemo(() => uniqueAttachments(attachments), [attachments]);
  if (!visible.length) return null;

  return (
    <>
      <div className="hc-message-attachments">
        {visible.map((attachment) => (
          attachment.type === "image" && attachment.src ? (
            <button
              type="button"
              className="hc-message-image"
              key={attachment.id}
              onClick={() => setActiveImage(attachment)}
            >
              <img src={attachment.src} alt={attachment.name} loading="lazy" />
              <span>{attachment.name}</span>
            </button>
          ) : (
            <button
              type="button"
              className="hc-message-file"
              key={attachment.id}
              onClick={() => void onCopy?.("附件路径", attachment.path)}
              title={attachment.path}
            >
              <FileText size={15} />
              <span>
                <strong>{attachment.name}</strong>
                {attachment.note ? <small>{attachment.note}</small> : null}
              </span>
              <Copy size={13} />
            </button>
          )
        ))}
      </div>

      {activeImage ? (
        <div className="hc-image-modal" role="dialog" aria-modal="true" aria-label="图片预览">
          <figure>
            <header>
              <span><ImageIcon size={15} />{activeImage.name}</span>
              <button type="button" aria-label="关闭图片预览" onClick={() => setActiveImage(null)}>
                <X size={15} />
              </button>
            </header>
            <img src={activeImage.src} alt={activeImage.name} />
            <figcaption>{activeImage.path}</figcaption>
          </figure>
        </div>
      ) : null}
    </>
  );
});
