# My Cooked Coke (student site) - React + Tailwind

The student site (my.cookedcoke.co) as a React app. Fizzy serves it for the student pages and it reads
everything through the same JSON the My CCC iOS app uses (`X-Fizzy-Format: json` on `/me`, `/me/login`,
`/me/c/<code>`, `/me/hunt`...), so the iOS app and this site always show the same thing.

## Working on it

```sh
cd student-web
npm install
npm run dev        # http://localhost:5173, talking to Fizzy on port 8000 (run Fizzy too)
npm run build      # into ../fizzy/static/me-app, which Fizzy serves
```

The Docker image builds it by itself, so pressing Update is enough to put changes live.
If the build is missing, or `FIZZY_STUDENT_UI=classic` is set in `.env`, Fizzy serves the original
server-drawn pages instead (fizzy/templates/me*.html), a safe fallback.

## Adding 21st.dev components

It's set up the way shadcn/21st.dev expect: the `@/` alias, `cn()` in `src/lib/utils.ts`, `components.json`,
Tailwind v4, `motion` and `lucide-react`. Copy a component's install command from 21st.dev and run it here:

```sh
npx shadcn@latest add "https://21st.dev/r/<author>/<component>"
```

It lands in `src/components/ui/`. The shadcn colour names (`bg-primary`, `text-muted-foreground`,
`bg-card`, `border-border`...) are mapped to the brand in `src/index.css`, so components come out in
Cooked Coke red, cream and sun yellow, light and dark. Brand names work too: `bg-red`, `bg-sun`,
`bg-cream`, `text-ink`, `text-muted`, `font-display` (Bricolage).

## Where things are

- `src/App.tsx`: the pages (sign in, home, guest hunt, a poster's link) and loading.
- `src/pages/`: Login, Home, Hunt (playing without signing in).
- `src/components/`: each part of the home page (points, rewards, tab, leaderboard, badges, challenges).
- `src/lib/api.ts`: the calls and the JSON's shape.
