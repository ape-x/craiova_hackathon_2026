import type { AppConfig, CommandScope, World } from './types';
import { voiceStartupScope } from './voiceScope';

export const platformViews = [
  'airport',
  'region',
  'passenger',
  'map',
  'resources',
  'inspector',
  'plan',
  'log',
] as const;
export const platformScreens = [
  'settings',
  'help',
  'search',
  'live-data',
  'recovery-case',
  'recovery-picker',
  'scenario',
  'new-scenario',
  'demo-setup',
  'voice-review',
  'reset',
  'passenger',
  'real-data',
  'arrival',
  'restrictions',
  'recovery',
  'results',
] as const;
export const liveDataTabs = [
  'search',
  'planning_transfer',
  'generate',
  'passengers',
  'transport',
  'hotels',
  'confirm',
] as const;
export const resourceTabs = ['flight', 'bus', 'groups', 'hotel'] as const;
export const timelineActions = ['earlier', 'later', 'now', 'zoom_in', 'zoom_out'] as const;
export type LiveDataNavigation = { request: number; tab: (typeof liveDataTabs)[number] };
export type ResourceNavigation = {
  request: number;
  tab?: (typeof resourceTabs)[number];
  query?: string;
  realData?: boolean;
};
export type SectionNavigation = {
  request: number;
  section: 'arrival' | 'restrictions' | 'recovery';
};
export type TimelineNavigation = { request: number; action: (typeof timelineActions)[number] };
export type PlatformAction =
  | {
      name: 'ui.navigate';
      arguments: {
        view: (typeof platformViews)[number];
        selected_id?: string;
        map_style?: 'architectural' | 'geographic';
        tab?: (typeof resourceTabs)[number];
        query?: string;
      };
    }
  | {
      name: 'ui.open';
      arguments: {
        screen: (typeof platformScreens)[number];
        tab?: (typeof liveDataTabs)[number];
        incident_id?: string;
        group_id?: string;
        query?: string;
      };
    }
  | { name: 'ui.theme'; arguments: { value: 'light' | 'dark' } }
  | { name: 'ui.planner'; arguments: { value: 'ai' | 'solver' } }
  | { name: 'ui.timeline'; arguments: { action: (typeof timelineActions)[number] } }
  | { name: 'ui.close' | 'ui.export' | 'ui.refresh'; arguments: Record<string, never> };

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid application action.');
  return value as Record<string, unknown>;
}
function choice<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T))
    throw new Error(`Unsupported ${name}.`);
  return value as T;
}
function optionalText(value: unknown, name: string, limit = 200): string | undefined {
  if (value == null) return undefined;
  if (typeof value !== 'string' || value.length > limit || !value.trim())
    throw new Error(`Invalid ${name}.`);
  return value;
}
function fields(args: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(args).some((key) => !allowed.includes(key)))
    throw new Error('Unsupported application action argument.');
}
export function hasPlatformEntity(world: World, id: string): boolean {
  return (
    [...world.airports, ...world.resources, ...world.groups].some((item) => item.id === id) ||
    world.observations.some((item) => String(item.id ?? item.fr24_id ?? '') === id)
  );
}

/** Accept a finite UI vocabulary. Server output never becomes a URL, script, selector or API path. */
export function validatePlatformAction(
  value: unknown,
  world: World,
  config: AppConfig,
): PlatformAction {
  const raw = record(value);
  fields(raw, ['name', 'arguments']);
  const args = record(raw.arguments);
  switch (raw.name) {
    case 'ui.navigate': {
      fields(args, ['view', 'selected_id', 'map_style', 'tab', 'query']);
      const view = choice(args.view, platformViews, 'view');
      const selected_id = optionalText(args.selected_id, 'selection');
      if (selected_id && !hasPlatformEntity(world, selected_id))
        throw new Error('The selected item is no longer in this operation.');
      const map_style =
        args.map_style == null
          ? undefined
          : choice(args.map_style, ['architectural', 'geographic'] as const, 'map style');
      if (map_style === 'architectural' && world.mode === 'live')
        throw new Error('The live operation uses geographic maps.');
      return {
        name: raw.name,
        arguments: {
          view,
          selected_id,
          map_style,
          tab: args.tab == null ? undefined : choice(args.tab, resourceTabs, 'resource tab'),
          query: optionalText(args.query, 'search', 2000),
        },
      };
    }
    case 'ui.open': {
      fields(args, ['screen', 'tab', 'incident_id', 'group_id', 'query']);
      const screen = choice(args.screen, platformScreens, 'screen');
      if (['scenario', 'new-scenario', 'reset'].includes(screen) && world.mode !== 'simulation')
        throw new Error('This screen is only available in a simulation.');
      if (
        [
          'live-data',
          'recovery-case',
          'recovery-picker',
          'arrival',
          'restrictions',
          'recovery',
        ].includes(screen) &&
        world.mode !== 'live'
      )
        throw new Error('This screen requires a live operation.');
      if (screen === 'demo-setup' && !world.demo)
        throw new Error('Test cases are only available in the demo workspace.');
      const incident_id = optionalText(args.incident_id, 'case');
      const group_id = optionalText(args.group_id, 'passenger group');
      if (incident_id && !world.incidents?.some((item) => item.id === incident_id))
        throw new Error('The recovery case is no longer available.');
      const group = group_id ? world.groups.find((item) => item.id === group_id) : undefined;
      if (group_id && !group) throw new Error('The passenger group is no longer available.');
      if (
        group &&
        incident_id &&
        (group.incident_id !== incident_id ||
          !world.incidents?.find((item) => item.id === incident_id)?.group_ids.includes(group.id))
      )
        throw new Error('The passenger group does not belong to this recovery case.');
      const tab =
        args.tab == null ? undefined : choice(args.tab, liveDataTabs, 'operation data tab');
      if (tab && screen !== 'live-data')
        throw new Error('Operation data tabs require the operation data screen.');
      if (tab && world.demo && ['passengers', 'transport', 'confirm'].includes(tab))
        throw new Error('This action is unavailable for fictional demo passengers.');
      return {
        name: raw.name,
        arguments: {
          screen,
          tab,
          incident_id,
          group_id,
          query: optionalText(args.query, 'search', 2000),
        },
      };
    }
    case 'ui.theme':
      fields(args, ['value']);
      return {
        name: raw.name,
        arguments: { value: choice(args.value, ['light', 'dark'] as const, 'theme') },
      };
    case 'ui.planner': {
      fields(args, ['value']);
      const value = choice(args.value, ['ai', 'solver'] as const, 'planner');
      if (value === 'ai' && !config.ai_configured)
        throw new Error('AI planning is not configured.');
      return { name: raw.name, arguments: { value } };
    }
    case 'ui.timeline':
      fields(args, ['action']);
      return {
        name: raw.name,
        arguments: { action: choice(args.action, timelineActions, 'timeline action') },
      };
    case 'ui.close':
    case 'ui.export':
    case 'ui.refresh':
      fields(args, []);
      return { name: raw.name, arguments: {} };
    default:
      throw new Error('Unsupported application action.');
  }
}

/** Resolve every request against current UI state, not the voice session's initial selection. */
export function platformCommandScope(
  world: World,
  selectedId: string | null,
  caseId: string | null,
  caseOpen: boolean,
): CommandScope {
  return {
    ...voiceStartupScope(world, selectedId, caseId, caseOpen),
    ...(world.mode === 'simulation' && world.groups.some((item) => item.id === selectedId)
      ? { group_id: selectedId! }
      : {}),
    ...(selectedId && hasPlatformEntity(world, selectedId) ? { selected_id: selectedId } : {}),
  };
}
