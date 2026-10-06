import type { TagCatalog, TagCategory, TagCategoryDefinition } from './api';
import { translate } from './i18n';

export const tagCategories: [TagCategory, string][] = [['author', '作者'], ['video_type', '视频类型'], ['axis_type', '轴类型'], ['release_type', '发布类型'], ['tier', '档位'], ['duration', '时间'], ['custom', '自定义分类']];
export const manualTagCategories = tagCategories.filter(([category]) => category !== 'duration');
const names = new Map<TagCategory, string>();

export function registerCategoryDefinitions(definitions: TagCategoryDefinition[] = []) {
  names.clear();
  definitions.forEach(definition => names.set(definition.category, definition.name));
}
/** Builtin labels translate; user-authored category names remain unchanged. */
export function categoryName(category: TagCategory, definitionsOrLabel?: TagCategoryDefinition[] | string): string {
  const builtin = tagCategories.find(([key]) => key === category);
  if (builtin) return translate(builtin[1]);
  return (typeof definitionsOrLabel === 'string' ? definitionsOrLabel : definitionsOrLabel?.find(definition => definition.category === category)?.name) || names.get(category) || category;
}

export function categoryChoices(catalog: TagCatalog | null, includeDuration = true): [TagCategory, string][] {
  const choices = tagCategories.filter(([category]) => includeDuration || category !== 'duration').map(([category, name]): [TagCategory, string] => [category, name]);
  const existing = new Set(choices.map(([category]) => category));
  for (const definition of catalog?.category_definitions || []) {
    if (existing.has(definition.category) || !includeDuration && definition.category === 'duration') continue;
    choices.push([definition.category, definition.name]); existing.add(definition.category);
  }
  // Work/tag DTOs can arrive before the full category catalog has loaded.
  for (const tag of catalog?.items || []) {
    if (tag.deleted || existing.has(tag.category) || !includeDuration && tag.category === 'duration') continue;
    choices.push([tag.category, tag.category_label || names.get(tag.category) || tag.category]); existing.add(tag.category);
  }
  return choices;
}
