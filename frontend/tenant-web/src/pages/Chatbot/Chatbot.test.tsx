/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose: Locking tests for Chatbot.tsx. Pins the input placeholder text so
*          "Type a message..." (three dots) cannot silently revert to two dots.
*/
import React from 'react';
import { render, screen } from '@testing-library/react';
import Chatbot from './Chatbot';

jest.mock('./Chatbot.scss', () => ({}));

jest.mock('@axa-fr/react-oidc', () => ({
    useOidcAccessToken: () => ({ accessToken: 'mock-token', accessTokenPayload: { oid: 'mock-oid' } }),
}));

jest.mock('../../hooks/theme', () => ({
    tokens: () => ({
        primary: { 400: '#fff' },
        grey: { 100: '#000' },
        greenAccent: { 400: '#0f0' },
    }),
}));

jest.mock('../../services/ChatBotService', () => ({
    postChatBot: jest.fn(),
}));

jest.mock('./Chats', () => () => <div data-testid="mock-chats" />);
jest.mock('./NextSteps', () => ({ NextSteps: () => <div data-testid="mock-next-steps" /> }));

describe('Chatbot — grammar-fix locking tests', () => {
    test('renders without crashing', () => {
        render(<Chatbot />);
    });

    test('message input placeholder is "Type a message..." (three dots)', () => {
        render(<Chatbot />);
        expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument();
    });

    test('old placeholder "Type a message.." (two dots) is absent', () => {
        render(<Chatbot />);
        expect(screen.queryByPlaceholderText('Type a message..')).not.toBeInTheDocument();
    });

    test('renders the FIELDCRAFT AI Chat heading', () => {
        render(<Chatbot />);
        expect(screen.getByText('FIELDCRAFT AI Chat!')).toBeInTheDocument();
    });
});
