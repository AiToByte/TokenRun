/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        surface: "oklch(98% 0 0)",
        accent: "oklch(68% 0.21 250)",
      },
    },
  },
  plugins: [],
};
