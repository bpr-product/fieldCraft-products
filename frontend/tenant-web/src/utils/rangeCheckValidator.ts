import { RangeCheck } from "../interface/SdmInterfaces";

export interface ValidationResult {
    isValid: boolean;
    isSoftWarning?: boolean;
    errorMessage?: string;
    failedCheck?: RangeCheck;
}

const comparatorViolates = (
    value: number,
    checkValue: number,
    comparator: string
): boolean => {
    switch (comparator) {
        case 'LT': 
            return value < checkValue;
        case 'GT': 
            return value > checkValue;
        case 'LE':
            return value <= checkValue;
        case 'GE': 
            return value >= checkValue;
        case 'EQ':
            return value === checkValue;
        case 'NE':
            return value !== checkValue;
        default:
            return false;
    }
};

export const validateRangeCheck = (
    value: string | number | undefined | null,
    rangeChecks?: RangeCheck[]
): ValidationResult => {

    if (value === undefined || value === null || value === '' || !rangeChecks || rangeChecks.length === 0) {
        return { isValid: true };
    }

    const numValue = typeof value === 'string' ? parseFloat(value) : value;
    const strValue = typeof value === 'string' ? value : String(value);

    for (const check of rangeChecks) {
        if (!check.CheckValue) continue;

        const checkValueNum = parseFloat(check.CheckValue);
        const isStringComparison = isNaN(checkValueNum);

        let violates: boolean;
        if (isStringComparison) {
            if (check.Comparator === 'EQ') {
                violates = strValue === check.CheckValue;
            } else if (check.Comparator === 'NE') {
                violates = strValue !== check.CheckValue;
            } else {
                continue;
            }
        } else {
            if (isNaN(numValue)) {
                return { isValid: false, errorMessage: "Please enter a valid number" };
            }
            violates = comparatorViolates(numValue, checkValueNum, check.Comparator);
        }

        if (violates) {
            const errorMsg = check.ErrorMessage?.TranslatedText?.text ||
                            `Value does not meet the ${check.Comparator} ${check.CheckValue} requirement`;
            const isSoft = check.SoftHard === 'Soft';

            return {
                isValid: isSoft,
                isSoftWarning: isSoft,
                errorMessage: errorMsg,
                failedCheck: check
            };
        }
    }

    return { isValid: true };
};

export const getComparatorDescription = (comparator: string, value: string): string => {
    switch (comparator) {
        case 'LT':
            return `must be less than ${value}`;
        case 'GT':
            return `must be greater than ${value}`;
        case 'LE':
            return `must be less than or equal to ${value}`;
        case 'GE':
            return `must be greater than or equal to ${value}`;
        case 'EQ':
            return `must be equal to ${value}`;
        case 'NE':
            return `must not be equal to ${value}`;
        default:
            return `must meet ${comparator} requirement`;
    }
};
