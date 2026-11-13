/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import {
  TkInput,
  TkTextarea,
  TkCheckbox,
  TkRadioGroup,
  TkRadioGroupItem,
  TkLabel,
  TkSelect,
  TkSelectTrigger,
  TkSelectContent,
  TkSelectItem,
  TkSelectGroup,
  TkSelectLabel,
  TkSelectValue,
} from 'thinkube-style/components/forms-inputs';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { Loader2 } from 'lucide-react';
import api from '@/lib/axios';
import { errorText, type CustomImage } from './types';

type Mode = 'from_base' | 'extend_existing';

/** A base image of GET /custom-images/base-registry, as the form offers it. */
interface BaseOption {
  id: string;
  label: string;
  jupyter: boolean;
  /** What build_config.base_image carries for this base image. */
  reference: string;
  /** The Dockerfile the server starts the image with; empty when it has none. */
  template: string;
}

interface RegistryEntry {
  id: string;
  name: string;
  display_name?: string;
  registry_url?: string | null;
  source: 'predefined' | 'built' | 'mirrored';
  type: 'jupyter' | 'standard';
  template?: string | null;
}

// The choice of the base image list that lets the user type any image address
const OTHER_BASE = 'other';

const NAME_RULE = /^[a-z0-9-]+$/;

/**
 * The server finds a predefined base image by its key, and a custom base
 * image that has a template by its id; any other base image is named by its
 * address in Harbor, which the server also writes after FROM.
 */
function baseOption(entry: RegistryEntry): BaseOption {
  const template = entry.template || '';
  const byId = entry.source === 'predefined' || (entry.source === 'built' && template !== '');
  return {
    id: entry.id,
    label: entry.display_name || entry.name,
    jupyter: entry.type === 'jupyter',
    reference: byId || !entry.registry_url ? entry.id : entry.registry_url,
    template,
  };
}

interface CreateCustomImageDialogProps {
  onCreated: (image: CustomImage) => void;
  onClose: () => void;
}

/**
 * Creates a custom image from a base image or from another custom image.
 * Every request names where the image starts: a base image, a parent image,
 * or both a base image and the Dockerfile written here. Mount it only while it is open.
 */
export function CreateCustomImageDialog({ onCreated, onClose }: CreateCustomImageDialogProps) {
  const { t } = useTranslation();
  const [bases, setBases] = useState<BaseOption[]>([]);
  const [parents, setParents] = useState<CustomImage[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>('from_base');
  const [name, setName] = useState('');
  const [scope, setScope] = useState('general');
  const [baseId, setBaseId] = useState('');
  const [otherBase, setOtherBase] = useState('');
  const [parentId, setParentId] = useState('');
  const [copyParent, setCopyParent] = useState(true);
  const [description, setDescription] = useState('');
  const [isBase, setIsBase] = useState(false);
  const [dockerfile, setDockerfile] = useState('');

  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let gone = false;
    (async () => {
      try {
        const [registry, builds] = await Promise.all([
          api.get('/custom-images/base-registry'),
          api.get('/custom-images', { params: { limit: 500 } }),
        ]);
        if (gone) return;
        const options: BaseOption[] = (registry.data.images as RegistryEntry[]).map(baseOption);
        setBases(options);
        setParents((builds.data.builds as CustomImage[]).filter((b) => b.is_base || b.status === 'success'));
        if (options.length > 0) {
          setBaseId(options[0].id);
          setDockerfile(options[0].template);
          setScope(options[0].jupyter ? 'jupyter' : 'general');
        } else {
          setBaseId(OTHER_BASE);
        }
      } catch (err) {
        if (!gone) setLoadError(errorText(err));
      }
    })();
    return () => {
      gone = true;
    };
  }, []);

  const base = bases.find((option) => option.id === baseId);
  const parent = parents.find((image) => image.id === parentId);
  const baseReference = baseId === OTHER_BASE ? otherBase.trim() : (base?.reference ?? '');
  const nameValid = NAME_RULE.test(name);
  const ready = nameValid && (mode === 'from_base' ? baseReference !== '' : !!parent);

  const chooseBase = (id: string) => {
    setBaseId(id);
    const chosen = bases.find((option) => option.id === id);
    setDockerfile(chosen?.template ?? '');
    if (chosen) setScope(chosen.jupyter ? 'jupyter' : 'general');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setWorking(true);
    setError(null);
    try {
      const common = { name, is_base: isBase };
      const build_config = { description, creation_mode: mode };
      // The Dockerfile is sent only when it was written here; the server applies a base image's own template itself
      const written = dockerfile.trim() !== '' && dockerfile !== (base?.template ?? '');
      const payload =
        mode === 'from_base'
          ? {
              ...common,
              scope,
              build_config: { ...build_config, base_image: baseReference },
              ...(written ? { dockerfile_content: dockerfile } : {}),
            }
          : {
              ...common,
              scope: parent!.scope,
              build_config,
              parent_image_id: parent!.id,
              copy_parent_dockerfile: copyParent,
            };
      const { data } = await api.post('/custom-images', payload);
      onCreated(data);
    } catch (err) {
      setError(errorText(err));
      setWorking(false);
    }
  };

  const jupyterBases = bases.filter((option) => option.jupyter);
  const standardBases = bases.filter((option) => !option.jupyter);
  const scopeIsFromBase = baseId !== OTHER_BASE;

  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onClose()}>
      <TkDialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <TkDialogHeader>
          <TkDialogTitle>{t('images.create.title')}</TkDialogTitle>
        </TkDialogHeader>

        <form onSubmit={submit} className="space-y-5">
          {loadError && <TkErrorAlert title={t('images.loadFailed')}>{loadError}</TkErrorAlert>}

          <div className="space-y-2">
            <TkLabel className="font-semibold">{t('images.create.mode')}</TkLabel>
            <TkRadioGroup value={mode} onValueChange={(value: string) => setMode(value as Mode)} className="flex gap-4">
              <div className="flex items-center gap-2">
                <TkRadioGroupItem value="from_base" id="create-from-base" />
                <TkLabel htmlFor="create-from-base" className="font-normal cursor-pointer">
                  {t('images.create.fromBase')}
                </TkLabel>
              </div>
              <div className="flex items-center gap-2">
                <TkRadioGroupItem value="extend_existing" id="create-extend" />
                <TkLabel htmlFor="create-extend" className="font-normal cursor-pointer">
                  {t('images.create.extend')}
                </TkLabel>
              </div>
            </TkRadioGroup>
          </div>

          <div className="space-y-2">
            <TkLabel htmlFor="create-name" className="font-semibold">
              {t('images.create.name')}
            </TkLabel>
            <TkInput
              id="create-name"
              value={name}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
              placeholder="my-custom-app"
              required
            />
            <p className={`text-sm ${name !== '' && !nameValid ? 'text-destructive' : 'text-muted-foreground'}`}>
              {t('images.create.nameHelp')}
            </p>
          </div>

          {mode === 'from_base' && (
            <>
              <div className="space-y-2">
                <TkLabel htmlFor="create-base" className="font-semibold">
                  {t('images.create.baseImage')}
                </TkLabel>
                <TkSelect value={baseId} onValueChange={(value: string) => chooseBase(value)}>
                  <TkSelectTrigger id="create-base">
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {jupyterBases.length > 0 && (
                      <TkSelectGroup>
                        <TkSelectLabel>{t('images.create.groupJupyter')}</TkSelectLabel>
                        {jupyterBases.map((option) => (
                          <TkSelectItem key={option.id} value={option.id}>
                            {option.label}
                          </TkSelectItem>
                        ))}
                      </TkSelectGroup>
                    )}
                    {standardBases.length > 0 && (
                      <TkSelectGroup>
                        <TkSelectLabel>{t('images.create.groupStandard')}</TkSelectLabel>
                        {standardBases.map((option) => (
                          <TkSelectItem key={option.id} value={option.id}>
                            {option.label}
                          </TkSelectItem>
                        ))}
                      </TkSelectGroup>
                    )}
                    <TkSelectItem value={OTHER_BASE}>{t('images.create.otherBase')}</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>
              </div>

              {baseId === OTHER_BASE && (
                <div className="space-y-2">
                  <TkLabel htmlFor="create-other-base" className="font-semibold">
                    {t('images.create.otherBaseLabel')}
                  </TkLabel>
                  <TkInput
                    id="create-other-base"
                    value={otherBase}
                    onChange={(e: ChangeEvent<HTMLInputElement>) => setOtherBase(e.target.value)}
                    placeholder="library/my-base:latest"
                    required
                  />
                </div>
              )}

              <div className="space-y-2">
                <TkLabel htmlFor="create-scope" className="font-semibold">
                  {t('images.create.type')}
                </TkLabel>
                <TkSelect value={scope} onValueChange={(value: string) => setScope(value)} disabled={scopeIsFromBase}>
                  <TkSelectTrigger id="create-scope">
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    <TkSelectItem value="general">{t('images.create.typeStandard')}</TkSelectItem>
                    <TkSelectItem value="jupyter">{t('images.create.typeJupyter')}</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>
                <p className="text-sm text-muted-foreground">
                  {t(scopeIsFromBase ? 'images.create.typeAuto' : 'images.create.typeHelp')}
                </p>
              </div>
            </>
          )}

          {mode === 'extend_existing' && (
            <div className="space-y-2">
              <TkLabel htmlFor="create-parent" className="font-semibold">
                {t('images.create.parent')}
              </TkLabel>
              {parents.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('images.create.noParents')}</p>
              ) : (
                <>
                  <TkSelect value={parentId} onValueChange={(value: string) => setParentId(value)}>
                    <TkSelectTrigger id="create-parent">
                      <TkSelectValue placeholder={t('images.create.parentPlaceholder')} />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      {parents.map((image) => (
                        <TkSelectItem key={image.id} value={image.id}>
                          {image.name} ({t(`images.status.${image.status}`, { defaultValue: image.status })})
                        </TkSelectItem>
                      ))}
                    </TkSelectContent>
                  </TkSelect>
                  <div className="flex items-center gap-2">
                    <TkCheckbox
                      id="create-copy-parent"
                      checked={copyParent}
                      onCheckedChange={(checked: boolean | 'indeterminate') => setCopyParent(checked === true)}
                    />
                    <TkLabel htmlFor="create-copy-parent" className="font-normal cursor-pointer">
                      {t('images.create.copyParent')}
                    </TkLabel>
                  </div>
                  <p className="text-sm text-muted-foreground">{t('images.create.copyParentHelp')}</p>
                </>
              )}
            </div>
          )}

          <div className="space-y-2">
            <TkLabel htmlFor="create-description" className="font-semibold">
              {t('images.create.description')}
            </TkLabel>
            <TkTextarea
              id="create-description"
              value={description}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)}
              placeholder={t('images.create.descriptionPlaceholder')}
              className="h-20"
            />
          </div>

          <div className="flex items-center gap-2">
            <TkCheckbox
              id="create-is-base"
              checked={isBase}
              onCheckedChange={(checked: boolean | 'indeterminate') => setIsBase(checked === true)}
            />
            <TkLabel htmlFor="create-is-base" className="font-normal cursor-pointer">
              {t('images.create.isBase')}
            </TkLabel>
          </div>

          {mode === 'from_base' && (
            <div className="space-y-2">
              <TkLabel htmlFor="create-dockerfile" className="font-semibold">
                {t('images.create.dockerfile')}
              </TkLabel>
              <TkTextarea
                id="create-dockerfile"
                value={dockerfile}
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDockerfile(e.target.value)}
                placeholder={t('images.create.dockerfilePlaceholder')}
                className="h-48 font-mono text-sm"
              />
              <p className="text-sm text-muted-foreground">{t('images.create.dockerfileHelp')}</p>
            </div>
          )}

          {error && <TkErrorAlert title={t('images.create.failed')}>{error}</TkErrorAlert>}

          <TkDialogFooter>
            <TkButton type="button" intent="secondary" onClick={onClose} disabled={working}>
              {t('common.cancel')}
            </TkButton>
            <TkButton type="submit" disabled={working || !ready} className="gap-2">
              {working && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('images.create.submit')}
            </TkButton>
          </TkDialogFooter>
        </form>
      </TkDialogContent>
    </TkDialogRoot>
  );
}
