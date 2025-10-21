import { createI18n } from 'vue-i18n'
import en from '../locales/en.json'
import es from '../locales/es.json'
import ca from '../locales/ca.json'
import fr from '../locales/fr.json'
import it from '../locales/it.json'
import de from '../locales/de.json'

// Get user's browser language
const getBrowserLanguage = () => {
  const lang = navigator.language || navigator.userLanguage
  // Extract the language code (e.g., 'en' from 'en-US')
  return lang.split('-')[0]
}

// Get saved language preference or browser language
const getInitialLocale = () => {
  const saved = localStorage.getItem('locale')
  if (saved) return saved
  
  const browserLang = getBrowserLanguage()
  const supportedLocales = ['en', 'es', 'ca', 'fr', 'it', 'de']
  
  return supportedLocales.includes(browserLang) ? browserLang : 'en'
}

// Create i18n instance with options
const i18n = createI18n({
  legacy: false, // Use Composition API mode
  locale: getInitialLocale(), // Default locale
  fallbackLocale: 'en', // Fallback locale
  messages: {
    en,
    es,
    ca,
    fr,
    it,
    de
  },
  // Enable HTML in translations (use with caution)
  allowComposition: true,
  // Silently fall back to default locale if translation is missing
  silentTranslationWarn: true,
  silentFallbackWarn: true
})

export default i18n