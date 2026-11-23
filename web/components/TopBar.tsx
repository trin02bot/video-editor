"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

export default function TopBar() {
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setEmail(s?.user?.email ?? null));
    return () => sub.subscription.unsubscribe();
  }, []);
  return (
    <header className="top">
      <a href="/" className="brand">STUDIO</a>
      <div className="row">
        {email ? (<>
          <span className="muted">{email}</span>
          <button className="ghost" onClick={async () => { await supabase.auth.signOut(); location.href = "/login"; }}>Sign out</button>
        </>) : <a href="/login">Sign in</a>}
      </div>
    </header>
  );
}
