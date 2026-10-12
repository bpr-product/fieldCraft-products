export const normalizeStr = (v: any) => {
  if (v === undefined || v === null) return '';
  return String(v).trim().toLowerCase();
};

export const isAreaEqual = (a: any, b: any) => {
  const na = Number(a || 0);
  const nb = Number(b || 0);
  return na === nb;
};

export const isAssetMatch = (current: any, asset: any) => {
  // Fields to compare
  // Exclude street address from the comparison per requirements
  const fields = ['name', 'area', 'city', 'state', 'country', 'zipCode'];

  for (const f of fields) {
    const cur = current[f];
    const other = asset[f];
    if (f === 'area') {
      if (!isAreaEqual(cur, other)) return false;
    } else {
      if (normalizeStr(cur) !== normalizeStr(other)) return false;
    }
  }
  return true;
};

export const findDuplicateAsset = (current: any, assets: any[] = [], ignoreAssetUid?: any) => {
  if (!current) return null;
  for (const a of assets) {
    if (ignoreAssetUid && (a.assetUid === ignoreAssetUid || a.id === ignoreAssetUid)) continue;
    if (isAssetMatch(current, a)) return a;
  }
  return null;
};

const assetDuplicate = {
  normalizeStr,
  isAreaEqual,
  isAssetMatch,
  findDuplicateAsset,
};

export default assetDuplicate;
