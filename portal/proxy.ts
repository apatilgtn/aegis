import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  if (process.env.NODE_ENV === "production" && request.nextUrl.pathname.startsWith("/auth/design-preview")) {
    return new NextResponse("Not found", { status: 404 });
  }
  // These endpoints authenticate a per-user bearer token in the route handler.
  if (request.nextUrl.pathname.startsWith("/api/v1/")) return NextResponse.next();
  return updateSession(request);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
