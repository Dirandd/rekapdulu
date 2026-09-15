/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    "./templates/**/*.html",
    "./static/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        primary: '#4F46E5',
        secondary: '#1E293B',
        success: '#059669',
        danger: '#DC2626',
        info: '#2563EB',
      }
    },
  },
  plugins: [],
}