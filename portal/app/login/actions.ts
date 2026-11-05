"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type AuthState = {
  error?: string;
  message?: string;
};

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function authenticate(_state: AuthState, formData: FormData): Promise<AuthState> {
  const intent = formData.get("intent") === "signup" ? "signup" : "signin";
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const password = String(formData.get("password") ?? "");
  const displayName = String(formData.get("displayName") ?? "").trim();

  if (!emailPattern.test(email) || password.length < 1) {
    return { error: "Enter a valid email address and password." };
  }

  if (intent === "signup" && (displayName.length < 2 || password.length < 12)) {
    return { error: "For a new account, enter your name and use at least 12 password characters." };
  }

  const supabase = await createClient();

  if (intent === "signup") {
    const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { display_name: displayName },
        emailRedirectTo: `${siteUrl.replace(/\/$/, "")}/auth/callback`,
      },
    });

    if (error) return { error: "We could not create the account. Check the details and try again." };
    if (!data.session) return { message: "Check your email to verify the account, then sign in." };
  } else {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) return { error: "The email or password is incorrect." };
  }

  redirect("/");
}
