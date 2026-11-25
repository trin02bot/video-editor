"use client";
import * as tus from "tus-js-client";
import { supabase } from "./supabase";

/** Resumable upload straight to Supabase Storage (handles multi-GB 4K files, survives dropped connections). */
export async function uploadResumable(bucket: string, path: string, file: File, onProgress: (pct: number) => void): Promise<void> {
  const { data } = await supabase.auth.getSession();
  if (!data.session?.access_token) throw new Error("not signed in");
  // Direct storage hostname: recommended by Supabase for large uploads (better throughput).
  const projectUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!.replace(".supabase.co", ".storage.supabase.co");
  return new Promise((resolve, reject) => {
    const up = new tus.Upload(file, {
      endpoint: `${projectUrl}/storage/v1/upload/resumable`,
      retryDelays: [0, 3000, 5000, 10000, 20000, 60000],
      headers: { "x-upsert": "true" },
      // Long 4K uploads outlast the 1-hour access token: sign every chunk with the current (auto-refreshed) session.
      onBeforeRequest: async (req) => {
        const { data: s } = await supabase.auth.getSession();
        if (s.session?.access_token) req.setHeader("authorization", `Bearer ${s.session.access_token}`);
      },
      uploadDataDuringCreation: true,
      removeFingerprintOnSuccess: true,
      metadata: { bucketName: bucket, objectName: path, contentType: file.type || "video/mp4", cacheControl: "3600" },
      chunkSize: 6 * 1024 * 1024, // Supabase requires exactly 6 MB chunks
      onError: reject,
      onProgress: (sent, total) => onProgress((sent / total) * 100),
      onSuccess: () => resolve(),
    });
    up.findPreviousUploads().then((prev) => {
      if (prev.length) up.resumeFromPreviousUpload(prev[0]);
      up.start();
    });
  });
}
