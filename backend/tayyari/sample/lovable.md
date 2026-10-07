# Recipe Genie — dinner from whatever is in your fridge

URL: https://recipe-genie.lovable.app
Description: Tell Recipe Genie what's in your kitchen and get three recipes you can cook tonight. Built with Lovable.
Built with: Lovable (Vite + React)
Lovable badge: "Edit with Lovable" present
Linked GitHub repository: github.com/riyakapoor/recipe-genie-web

## Libraries and services detected
- React + Vite (Lovable default stack)
- Tailwind CSS
- shadcn/ui components (Radix primitives: dialog, tabs, toast, dropdown-menu)
- Supabase JS client: email magic-link auth, Postgres tables via REST (recipes, pantry_items, favourites)
- OpenAI API (chat completions) called from a Supabase Edge Function `generate-recipes`
- lucide-react icons
- No analytics or product-tracking script detected (no GA, PostHog, Plausible, Mixpanel)
- No error-monitoring script detected

## Landing page (visible text)
Hero: "Dinner, sorted. From whatever's in your fridge."
Sub: "Add what you have. Recipe Genie suggests three recipes you can actually cook tonight — with swaps for what's missing."
Primary button: "Try it free" · Secondary: "See an example"
Social proof line: "Loved by 600+ home cooks"
Feature cards:
- "Pantry in seconds" — type or paste your ingredients; Genie understands "half an onion" and "leftover rice".
- "Three good options" — quick, comfort, or healthy, each with time, difficulty and a shopping gap list.
- "Swap anything" — no cream? Genie rewrites the step with what you have.
- "Save your favourites" — keep the recipes that worked and rate them.
Footer: "Made by Riya Kapoor · riyakapoor.design · Built with Lovable"

## App screens and user flows
Flow 1 — Sign in: email magic link (Supabase auth). Empty state after sign-in: "Your pantry is empty. Add 3+ ingredients to start."
Flow 2 — Generate: pantry input with chips → "Ask the Genie" button → loading state with rotating messages
("Checking the spice rack…") → three recipe cards (Quick / Comfort / Healthy) with time, difficulty and "missing: 2 items".
Recipe detail: ingredients, numbered steps, "Swap an ingredient" dialog that regenerates one step.
Flow 3 — Save & share: heart to save into Favourites; public share link per recipe (/r/:id) with Open Graph preview.

## Prompt-driven UI notes
- The generate screen shows the dietary preferences that are sent with the prompt (vegetarian, no nuts, under 30 min).
- Recipes come back as structured JSON rendered into cards (fields: title, time, difficulty, ingredients, steps, swaps).
- Regenerate button on each card; no visible rating of output quality beyond the favourite heart.
- Error state: a toast "The Genie is tired, try again" when generation fails; no retry with backoff.

## Other observations from the page
- Settings page: dietary preferences and units (metric/imperial). No onboarding tour.
- No privacy policy or terms page linked.
- Lighthouse-style signals: images are lazy-loaded; the landing page ships ~410 KB of JS.
