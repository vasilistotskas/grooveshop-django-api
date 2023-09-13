/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./core/templates/**/*.html'],
  theme: {
    extend: {
      gridTemplateColumns: {
        'auto-1fr': 'auto 1fr',
        '1fr-auto': '1fr auto',
        'auto-auto': 'auto auto',
        'auto-fill-150': 'repeat(auto-fill, minmax(150px, 1fr));',
        '3fr-2fr': '3fr 2fr',
      },
      gridTemplateRows: {
        'auto-1fr': 'auto 1fr',
        '1fr-auto': '1fr auto',
        'auto-auto': 'auto auto',
        'auto-fill-150': 'repeat(auto-fill, minmax(150px, 1fr));',
        '3fr-2fr': '3fr 2fr',
      },
      spacing: {
        '60px': '60px',
      },
      colors: {
        primary: 'rgb(146 56 56)',
      },
    }
  },
  plugins: [],
}
