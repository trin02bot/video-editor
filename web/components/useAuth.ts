"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

/** Redirects to /login when signed out; returns the user id once known. */
export function useAuth() {
  const [uid, setUid] = useState<string | null>(null);
  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      if (!data.user) location.href = "/login"; else setUid(data.user.id);
    });
  }, []);
  return uid;
}
