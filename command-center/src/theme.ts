export interface ThemeVars {
  '--color-bg': string
  '--color-panel': string
  '--color-panel2': string
  '--color-edge': string
  '--color-ink': string
  '--color-mut': string
  '--color-dim': string
  '--color-accent': string
  '--color-good': string
  '--color-warn': string
  '--color-bad': string
  '--color-codex': string
  '--color-claude': string
  '--color-anti': string
  '--color-pos': string
}

export interface Theme {
  id: string
  name: string
  vars: ThemeVars
}

export const THEMES: Theme[] = [
  {
    id: 'midnight',
    name: 'Midnight',
    vars: {
      '--color-bg':     '#080b10',
      '--color-panel':  '#0f1520',
      '--color-panel2': '#141c2a',
      '--color-edge':   '#1f2d3f',
      '--color-ink':    '#e2eaf4',
      '--color-mut':    '#8898ae',
      '--color-dim':    '#55657a',
      '--color-accent': '#4da3ff',
      '--color-good':   '#3dcf82',
      '--color-warn':   '#f5b545',
      '--color-bad':    '#f76d6d',
      '--color-codex':  '#c792ea',
      '--color-claude': '#d98a5b',
      '--color-anti':   '#5bd6c4',
      '--color-pos':    '#4da3ff',
    },
  },
  {
    id: 'nord',
    name: 'Nord',
    vars: {
      '--color-bg':     '#1c2130',
      '--color-panel':  '#222b3a',
      '--color-panel2': '#2b3548',
      '--color-edge':   '#3a4a60',
      '--color-ink':    '#d8dee9',
      '--color-mut':    '#8899ab',
      '--color-dim':    '#5c6e82',
      '--color-accent': '#88c0d0',
      '--color-good':   '#a3be8c',
      '--color-warn':   '#ebcb8b',
      '--color-bad':    '#bf616a',
      '--color-codex':  '#b48ead',
      '--color-claude': '#d08770',
      '--color-anti':   '#8fbcbb',
      '--color-pos':    '#81a1c1',
    },
  },
  {
    id: 'dracula',
    name: 'Dracula',
    vars: {
      '--color-bg':     '#181a26',
      '--color-panel':  '#21232f',
      '--color-panel2': '#282a36',
      '--color-edge':   '#383a4a',
      '--color-ink':    '#f8f8f2',
      '--color-mut':    '#9299ac',
      '--color-dim':    '#5e6478',
      '--color-accent': '#bd93f9',
      '--color-good':   '#50fa7b',
      '--color-warn':   '#f1fa8c',
      '--color-bad':    '#ff5555',
      '--color-codex':  '#ff79c6',
      '--color-claude': '#ffb86c',
      '--color-anti':   '#8be9fd',
      '--color-pos':    '#bd93f9',
    },
  },
  {
    id: 'catppuccin',
    name: 'Catppuccin Mocha',
    vars: {
      '--color-bg':     '#11111b',
      '--color-panel':  '#181825',
      '--color-panel2': '#1e1e2e',
      '--color-edge':   '#313244',
      '--color-ink':    '#cdd6f4',
      '--color-mut':    '#7f849c',
      '--color-dim':    '#585b70',
      '--color-accent': '#89b4fa',
      '--color-good':   '#a6e3a1',
      '--color-warn':   '#f9e2af',
      '--color-bad':    '#f38ba8',
      '--color-codex':  '#cba6f7',
      '--color-claude': '#fab387',
      '--color-anti':   '#94e2d5',
      '--color-pos':    '#89dceb',
    },
  },
  {
    id: 'solarized',
    name: 'Solarized Dark',
    vars: {
      '--color-bg':     '#00212b',
      '--color-panel':  '#002731',
      '--color-panel2': '#073642',
      '--color-edge':   '#124857',
      '--color-ink':    '#93a1a1',
      '--color-mut':    '#657b83',
      '--color-dim':    '#4a5e65',
      '--color-accent': '#268bd2',
      '--color-good':   '#859900',
      '--color-warn':   '#b58900',
      '--color-bad':    '#dc322f',
      '--color-codex':  '#6c71c4',
      '--color-claude': '#cb4b16',
      '--color-anti':   '#2aa198',
      '--color-pos':    '#268bd2',
    },
  },
  {
    id: 'gruvbox',
    name: 'Gruvbox',
    vars: {
      '--color-bg':     '#181818',
      '--color-panel':  '#222222',
      '--color-panel2': '#282828',
      '--color-edge':   '#3c3836',
      '--color-ink':    '#ebdbb2',
      '--color-mut':    '#a89984',
      '--color-dim':    '#7c6f64',
      '--color-accent': '#83a598',
      '--color-good':   '#b8bb26',
      '--color-warn':   '#fabd2f',
      '--color-bad':    '#fb4934',
      '--color-codex':  '#d3869b',
      '--color-claude': '#fe8019',
      '--color-anti':   '#8ec07c',
      '--color-pos':    '#83a598',
    },
  },
  {
    id: 'synthwave',
    name: 'Synthwave',
    vars: {
      '--color-bg':     '#0d0221',
      '--color-panel':  '#120332',
      '--color-panel2': '#1a063f',
      '--color-edge':   '#2e0f5a',
      '--color-ink':    '#f0e8ff',
      '--color-mut':    '#9b7fc7',
      '--color-dim':    '#5e3d8a',
      '--color-accent': '#f72585',
      '--color-good':   '#06d6a0',
      '--color-warn':   '#ffd60a',
      '--color-bad':    '#ff3860',
      '--color-codex':  '#b5179e',
      '--color-claude': '#ff9f1c',
      '--color-anti':   '#4cc9f0',
      '--color-pos':    '#7209b7',
    },
  },
  {
    id: 'paper',
    name: 'Paper',
    vars: {
      '--color-bg':     '#f5f0e8',
      '--color-panel':  '#ede8df',
      '--color-panel2': '#e4ddd3',
      '--color-edge':   '#ccc4b8',
      '--color-ink':    '#24201a',
      '--color-mut':    '#6b6055',
      '--color-dim':    '#9c9082',
      '--color-accent': '#0055cc',
      '--color-good':   '#1a7a3f',
      '--color-warn':   '#b05d00',
      '--color-bad':    '#c42020',
      '--color-codex':  '#7a3dbf',
      '--color-claude': '#a84a00',
      '--color-anti':   '#006b6b',
      '--color-pos':    '#0055cc',
    },
  },
]

const STORAGE_KEY = 'cc-theme'
const DEFAULT_THEME = 'midnight'

export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  for (const [prop, value] of Object.entries(theme.vars)) {
    root.style.setProperty(prop, value)
  }
}

export function loadTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY)
  const found = THEMES.find((t) => t.id === saved) ?? THEMES.find((t) => t.id === DEFAULT_THEME) ?? THEMES[0]
  return found
}

export function saveTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme.id)
}
