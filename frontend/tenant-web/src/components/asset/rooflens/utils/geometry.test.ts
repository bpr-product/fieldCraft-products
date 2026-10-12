import {
  sphericalAreaSqm,
  footprintSqft,
  perimeterFt,
  pitchFactor,
  parsePitchRise,
  parseLatLngStr,
} from "./geometry";
import { LatLng } from "../types";

const square: LatLng[] = [
  { lat: 0, lng: 0 },
  { lat: 0, lng: 0.0009 },
  { lat: 0.0009, lng: 0.0009 },
  { lat: 0.0009, lng: 0 },
];

describe("geometry.pitchFactor", () => {
  it("is 1 for a flat roof and √2 for 12/12", () => {
    expect(pitchFactor(0)).toBeCloseTo(1, 6);
    expect(pitchFactor(12)).toBeCloseTo(Math.SQRT2, 6);
  });
  it("increases monotonically with pitch", () => {
    expect(pitchFactor(6)).toBeGreaterThan(pitchFactor(0));
    expect(pitchFactor(12)).toBeGreaterThan(pitchFactor(6));
  });
});

describe("geometry.parsePitchRise", () => {
  it('parses "6/12" → 6', () => expect(parsePitchRise("6/12")).toBe(6));
  it("defaults to 6 for missing/invalid input", () => {
    expect(parsePitchRise(undefined)).toBe(6);
    expect(parsePitchRise("not-a-pitch")).toBe(6);
  });
});

describe("geometry.parseLatLngStr", () => {
  it('parses a valid "lat,lng"', () => {
    expect(parseLatLngStr("43.09,-89.28")).toEqual({ lat: 43.09, lng: -89.28 });
  });
  it("returns null for missing / malformed / out-of-range", () => {
    expect(parseLatLngStr(undefined)).toBeNull();
    expect(parseLatLngStr("nope")).toBeNull();
    expect(parseLatLngStr("999,999")).toBeNull();
  });
});

describe("geometry area & perimeter", () => {
  it("computes positive area and perimeter for a polygon", () => {
    expect(sphericalAreaSqm(square)).toBeGreaterThan(0);
    expect(perimeterFt(square)).toBeGreaterThan(0);
  });
  it("footprintSqft is the spherical area (m²) converted to ft²", () => {
    expect(footprintSqft(square)).toBeCloseTo(sphericalAreaSqm(square) * 10.7639104, 3);
  });
  it("returns 0 for degenerate input", () => {
    expect(sphericalAreaSqm([{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }])).toBe(0); // < 3 points
    expect(perimeterFt([{ lat: 0, lng: 0 }])).toBe(0); // < 2 points
  });
});
