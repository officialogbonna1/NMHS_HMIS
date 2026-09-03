export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // NMHS brand palette — navy header down to periwinkle-blue accents.
        brand: {
          50: "#EEF3FF",
          100: "#DCE6FF",
          200: "#B9CCFF",
          300: "#93B0FA",
          400: "#6E93F7",
          500: "#4C74EE",
          600: "#3457D1",
          700: "#2A44AC",
          800: "#1E2F7E",
          900: "#142057",
          950: "#0B1730",
        },
      },
      keyframes: {
        "toast-in": {
          "0%": { opacity: "0", transform: "translateY(-8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "toast-in": "toast-in 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
