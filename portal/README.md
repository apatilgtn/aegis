# Aegis Portal

Authenticated product portal for the Aegis Engineering Control Plane.

## Stack

- Next.js 16 App Router
- React 19
- TypeScript
- Tailwind CSS 4
- Lucide icons
- Supabase Auth, Postgres and Row Level Security

## Run

1. Copy `.env.example` to `.env.local` and provide the Supabase project URL and publishable key.
2. Use Node.js 22.13 or newer.
3. Install and run:

   ```bash
   npm ci
   npm run dev
   ```

Open `http://localhost:3000`.

Authentication and tenant membership are real. A verified user receives no workspace role automatically; an administrator must insert an active `tenant_memberships` record. Dashboard metrics and service examples remain representative until their individual modules are connected to live catalog and workflow queries.

Run `npm run check` before shipping. It performs linting, strict TypeScript validation and a production build.
