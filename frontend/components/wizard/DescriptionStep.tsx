'use client';

import { useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Mic, MicOff, Sparkles } from 'lucide-react';
import { useWizardStore } from '@/lib/store/wizardStore';

const suggestions: Record<string, string[]> = {
  website: [
    'A modern agency website with services, team, and contact page',
    'A restaurant website with menu, reservations, and photo gallery',
    'A SaaS landing page with pricing, features, and testimonials',
  ],
  ecommerce: [
    'An online clothing store with categories, cart, and checkout',
    'A digital products marketplace with instant downloads',
    'A food delivery platform with restaurant listings',
  ],
  dashboard: [
    'A sales analytics dashboard with charts and KPIs',
    'A project management tool with tasks and timelines',
    'An admin panel for managing users and content',
  ],
  app: [
    'A task management app with boards and drag-and-drop',
    'A social media platform with posts, likes, and comments',
    'A booking system for appointments and scheduling',
  ],
  portfolio: [
    'A photography portfolio with fullscreen gallery and categories',
    'A developer portfolio with projects, skills, and blog',
    'A design portfolio with case studies and process',
  ],
  blog: [
    'A tech blog with categories, search, and newsletter signup',
    'A travel blog with maps, photos, and trip guides',
    'A news site with featured articles and trending topics',
  ],
};

export function DescriptionStep() {
  const { description, setDescription, category } = useWizardStore();
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);
  const maxLength = 2000;
  const categorySuggestions = category ? suggestions[category] || [] : [];

  const toggleVoice = () => {
    if (isListening && recognitionRef.current) {
      recognitionRef.current.stop();
      setIsListening(false);
      return;
    }

    const SpeechRecognition = window.SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event: any) => {
      let transcript = '';
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      setDescription(description + ' ' + transcript);
    };

    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => setIsListening(false);

    recognition.start();
    recognitionRef.current = recognition;
    setIsListening(true);
  };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <h2 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 600, textAlign: 'center' }}>
        Tell us more about your project
      </h2>
      <p style={{ margin: '0 0 32px', fontSize: '16px', color: 'var(--text-secondary)', textAlign: 'center' }}>
        Describe what you want in your own words — any language works
      </p>

      <div style={{ maxWidth: '640px', margin: '0 auto' }}>
        <div style={{ position: 'relative' }}>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value.slice(0, maxLength))}
            placeholder="Describe your project... For example: I want a modern portfolio website with a dark theme, project gallery, about me section, and a contact form."
            dir="auto"
            style={{
              width: '100%',
              minHeight: '160px',
              padding: '16px',
              paddingRight: '52px',
              borderRadius: 'var(--radius-md)',
              border: '2px solid var(--border-light)',
              backgroundColor: 'var(--bg-input)',
              fontSize: '15px',
              lineHeight: 1.6,
              resize: 'vertical',
              outline: 'none',
              transition: 'border-color 0.15s',
            }}
            onFocus={(e) => (e.target.style.borderColor = 'var(--accent)')}
            onBlur={(e) => (e.target.style.borderColor = 'var(--border-light)')}
          />
          <button
            onClick={toggleVoice}
            title={isListening ? 'Stop listening' : 'Voice input'}
            style={{
              position: 'absolute',
              top: '12px',
              right: '12px',
              width: '36px',
              height: '36px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: isListening ? 'var(--error)' : 'var(--bg-tertiary)',
              color: isListening ? 'white' : 'var(--text-secondary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all 0.15s',
            }}
          >
            {isListening ? <MicOff size={18} /> : <Mic size={18} />}
          </button>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px', fontSize: '13px', color: 'var(--text-tertiary)' }}>
          <span>{description.length > 0 ? `${description.length}/${maxLength}` : ''}</span>
          {description.length === 0 && <span>Required</span>}
        </div>

        {categorySuggestions.length > 0 && description.length === 0 && (
          <div style={{ marginTop: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Sparkles size={14} /> Ideas for inspiration
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {categorySuggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => setDescription(suggestion)}
                  style={{
                    padding: '12px 16px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)',
                    backgroundColor: 'var(--bg-secondary)',
                    fontSize: '14px',
                    color: 'var(--text-primary)',
                    textAlign: 'left',
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                  onMouseOver={(e) => {
                    e.currentTarget.style.borderColor = 'var(--accent)';
                    e.currentTarget.style.backgroundColor = 'var(--bg-accent-light)';
                  }}
                  onMouseOut={(e) => {
                    e.currentTarget.style.borderColor = 'var(--border-light)';
                    e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}
