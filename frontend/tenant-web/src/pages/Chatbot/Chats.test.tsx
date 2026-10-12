import React from 'react';
import { render, screen } from '@testing-library/react';
import Chats from './Chats';

// setupTests.ts initializes i18next with the real en.json, so t() renders English.
jest.mock('./Chats.scss', () => ({}));

// jsdom does not implement Element.scrollTo; the component's auto-scroll effect calls it.
beforeAll(() => {
  if (!(Element.prototype as any).scrollTo) {
    (Element.prototype as any).scrollTo = () => { /* no-op */ };
  }
});

const botResponse = { purpose: '', message: '', sender: 'FieldCraft' };

describe('Chats', () => {
  it('renders the localized bot greeting on mount', () => {
    render(
      <Chats userResponse="" botResponse={botResponse} sendUserResponse="" optionClick={jest.fn()} />
    );
    expect(
      screen.getByText('Hello! Greetings from FIELDCRAFT! How can I help you?')
    ).toBeInTheDocument();
  });

  it('renders the localized "Today" timestamp label', () => {
    render(
      <Chats userResponse="" botResponse={botResponse} sendUserResponse="" optionClick={jest.fn()} />
    );
    // Full rendered format: "Today <h>:<mm> AM|PM" — asserts the localized label
    // prefix and the timestamp shape together, not a loose case-sensitive substring.
    expect(screen.getByText(/^Today \d{1,2}:\d{2} (AM|PM)$/)).toBeInTheDocument();
  });
});
