import type { Mappings, SDM, TemplateVersion } from '../interface/SdmInterfaces';

type Mergeable = { OID: string };

function parseMdvNumber(oid: string | undefined): number {
  if (!oid) return 0;
  const match = oid.match(/(\d+)/);
  return match ? parseInt(match[1], 10) : 0;
}

function mergeArr<T extends Mergeable>(base: T[] | undefined, delta: T[] | undefined): T[] {
  const map = new Map((base ?? []).map((item) => [item.OID, item]));
  (delta ?? []).forEach((item) => map.set(item.OID, item));
  return Array.from(map.values());
}

export function mergeTemplateVersions(
  base: TemplateVersion,
  delta: TemplateVersion
): TemplateVersion {
  const mergedMappings: Mappings[] =
    (delta.Mappings && delta.Mappings.length > 0)
      ? delta.Mappings
      : (base.Mappings ?? []);

  const merged: TemplateVersion = {
    ...delta,
    ItemDef: mergeArr(base.ItemDef, delta.ItemDef),
    ItemGroupDef: mergeArr(base.ItemGroupDef, delta.ItemGroupDef),
    FormDef: mergeArr(base.FormDef, delta.FormDef),
    EventDef: mergeArr(base.EventDef, delta.EventDef),
    CodeList: mergeArr(base.CodeList, delta.CodeList),
    ConditionDef: mergeArr(base.ConditionDef, delta.ConditionDef),
    Mappings: mergedMappings,
  };

  const baseMethodDef = base.MethodDef;
  const deltaMethodDef = delta.MethodDef;
  if (baseMethodDef || deltaMethodDef) {
    merged.MethodDef = mergeArr(baseMethodDef, deltaMethodDef);
  }

  const deltaProtocol = delta.Protocol?.EventRef;
  const baseProtocol = base.Protocol?.EventRef;
  if (!deltaProtocol?.length && baseProtocol?.length) {
    merged.Protocol = base.Protocol;
  }

  const deltaImages = delta.Images;
  const baseImages = base.Images;
  if (!deltaImages?.length && baseImages?.length) {
    merged.Images = baseImages;
  }

  return merged;
}

function createEmptyTemplateVersion(): TemplateVersion {
  return {
    Protocol: { EventRef: [] },
    EventDef: [],
    FormDef: [],
    ItemGroupDef: [],
    ItemDef: [],
    CodeList: [],
    ConditionDef: [],
    Mappings: [],
    OID: 'MDV.1',
    Name: 'MetaDataVersion',
    MethodDef: [],
  };
}


function pruneToFormDefReferences(merged: TemplateVersion): TemplateVersion {
  const referencedGroupOIDs = new Set<string>();
  (merged.FormDef ?? []).forEach((fd) => {
    (fd.ItemGroupRef ?? []).forEach((igRef) => {
      if (igRef.ItemGroupOID) {
        referencedGroupOIDs.add(igRef.ItemGroupOID);
      }
    });
  });

  if (referencedGroupOIDs.size === 0) return merged;

  return {
    ...merged,
    ItemGroupDef: (merged.ItemGroupDef ?? []).filter((ig) =>
      referencedGroupOIDs.has(ig.OID)
    ),
  };
}

export function resolveMergedTemplateVersion(
  versions: TemplateVersion[] | null | undefined
): TemplateVersion {
  if (!versions || versions.length === 0) {
    return createEmptyTemplateVersion();
  }
  if (versions.length === 1) {
    return pruneToFormDefReferences(versions[0]);
  }

  const sorted = [...versions].sort(
    (a, b) => parseMdvNumber(a.OID) - parseMdvNumber(b.OID)
  );

  const merged = sorted.reduce((acc, version) => mergeTemplateVersions(acc, version));
  return pruneToFormDefReferences(merged);
}

export function getActiveMetaDataVersionOID(sdm: SDM | any): string {
  const fromBuildingData = sdm?.SDM?.BuildingData?.MetaDataVersionOID;
  if (fromBuildingData) {
    return fromBuildingData;
  }

  const versions: TemplateVersion[] = sdm?.SDM?.Building?.TemplateVersion ?? [];
  if (versions.length === 0) {
    return 'MDV.1';
  }

  const sorted = [...versions].sort(
    (a, b) => parseMdvNumber(a.OID) - parseMdvNumber(b.OID)
  );
  return sorted[sorted.length - 1]?.OID ?? 'MDV.1';
}
  
export function getMergedTemplateVersionFromSdm(sdm: SDM | any): TemplateVersion {
  return resolveMergedTemplateVersion(sdm?.SDM?.Building?.TemplateVersion);
}

export function extractMetaDataVersionOIDFromAnswers(answers: unknown): string | undefined {
  if (answers == null || answers === '') {
    return undefined;
  }
  try {
    const data = typeof answers === 'string' ? JSON.parse(answers) : answers;
    if (data?.MetaDataVersionOID) {
      return data.MetaDataVersionOID;
    }
    if (data?.SDM?.BuildingData?.MetaDataVersionOID) {
      return data.SDM.BuildingData.MetaDataVersionOID;
    }
    if (data?.SDM?.Building?.TemplateVersion?.length) {
      return getActiveMetaDataVersionOID(data);
    }
  } catch {
    return undefined;
  }
  return undefined;
}
