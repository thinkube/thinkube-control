import { useState, useEffect, useMemo, useRef } from 'react'
import { useHarborStore } from '../stores/useHarborStore'
import axios from 'axios'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkInput, TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue, TkCheckbox, TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkTable, TkTableHeader, TkTableBody, TkTableRow, TkTableHead, TkTableCell } from 'thinkube-style/components/tables'
import { TkTabs } from 'thinkube-style/components/navigation'
import { TkStatCard } from 'thinkube-style/components/cards-data'
import { TkLoader } from 'thinkube-style/components/feedback'
import { TkErrorAlert } from 'thinkube-style/components/feedback'
import { TkDialogRoot, TkDialogContent, TkDialogHeader, TkDialogTitle, TkDialogFooter } from 'thinkube-style/components/modals-overlays'
import { TkCodeBlock } from 'thinkube-style/components/feedback'
import {
  RefreshCw,
  Plus,
  Eye,
  Trash2,
  Lock,
  Edit2,
  Settings,
  FileText,
  Box,
  Star,
  AlertCircle
} from 'lucide-react'
import { AddImageModal } from '../components/AddImageModal'
import { ViewImageModal } from '../components/harbor/ViewImageModal'
import { CreateCustomImageModal } from '../components/CreateCustomImageModal'
import BuildExecutor from '../components/BuildExecutor'

interface HarborImage {
  id: string
  name: string
  repository?: string
  tag: string
  digest?: string
  category: 'system' | 'user' | 'custom'
  description?: string
  mirror_date?: string
  protected: boolean
  is_base: boolean
  source?: string
  vulnerabilities?: {
    critical: number
    high: number
  }
}

interface CustomImage {
  id: string
  name: string
  scope: string
  status: 'pending' | 'building' | 'success' | 'failed'
  is_base: boolean
  parent_image_id?: string
  registry_url?: string
  created_at: string
  output?: string
  build_config?: {
    description?: string
  }
  children?: CustomImage[]
}

export function HarborImages() {
  const store = useHarborStore()

  // Tab state
  const [activeTab, setActiveTab] = useState<'mirrored' | 'custom'>('mirrored')

  // Modal states
  const [showAddImageModal, setShowAddImageModal] = useState(false)
  const [showViewImageModal, setShowViewImageModal] = useState(false)
  const [showCreateCustomImageModal, setShowCreateCustomImageModal] = useState(false)
  const [selectedImage, setSelectedImage] = useState<HarborImage | null>(null)
  const [selectedImageName, setSelectedImageName] = useState('')

  // Logs modal state
  const [showLogsModal, setShowLogsModal] = useState(false)
  const [logsContent, setLogsContent] = useState('')
  const [logsImageName, setLogsImageName] = useState('')
  const [logsFilePath, setLogsFilePath] = useState('')

  // Reference to BuildExecutor component
  const buildExecutorRef = useRef<any>(null)

  // Custom images state
  const [customImages, setCustomImages] = useState<CustomImage[]>([])
  const [customImagesLoading, setCustomImagesLoading] = useState(false)
  const [customImagesError, setCustomImagesError] = useState<string | null>(null)
  const [customImageScope, setCustomImageScope] = useState('')
  const [showOnlyBaseImages, setShowOnlyBaseImages] = useState(false)
  const [showTreeView, setShowTreeView] = useState(false)

  // Filter states
  const [selectedCategory, setSelectedCategory] = useState('')
  const [selectedProtected, setSelectedProtected] = useState('')
  const [searchQuery, setSearchQuery] = useState('')

  // Computed
  const hasActiveFilters = useMemo(() => {
    return !!selectedCategory || !!selectedProtected || !!searchQuery
  }, [selectedCategory, selectedProtected, searchQuery])

  // Filtered custom images based on scope and base image filter
  const filteredCustomImages = useMemo(() => {
    let filtered = customImages

    if (customImageScope) {
      filtered = filtered.filter(img => img.scope === customImageScope)
    }

    if (showOnlyBaseImages) {
      filtered = filtered.filter(img => img.is_base)
    }

    return filtered
  }, [customImages, customImageScope, showOnlyBaseImages])

  // Build image tree for tree view
  const imageTree = useMemo(() => {
    if (!showTreeView) return []

    const rootImages = filteredCustomImages.filter(img => !img.parent_image_id)

    const buildTree = (images: CustomImage[]): CustomImage[] => {
      return images.map(image => {
        const children = filteredCustomImages.filter(
          child => child.parent_image_id === image.id
        )
        return {
          ...image,
          children: children.length > 0 ? buildTree(children) : []
        }
      })
    }

    return buildTree(rootImages)
  }, [showTreeView, filteredCustomImages])

  const currentPage = useMemo(() => {
    return Math.floor((store.pagination?.skip || 0) / (store.pagination?.limit || 10)) + 1
  }, [store.pagination])

  const totalPages = useMemo(() => {
    return Math.ceil((store.pagination?.total || 0) / (store.pagination?.limit || 10))
  }, [store.pagination])

  // Methods
  const formatDate = (dateString?: string) => {
    if (!dateString) return '-'
    const date = new Date(dateString)
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString()
  }

  const syncImages = async () => {
    try {
      await store.syncWithHarbor()
      await store.fetchImageStats()
    } catch (error) {
      console.error('Failed to sync images:', error)
    }
  }

  const filterByCategory = (value: string) => {
    setSelectedCategory(value)
    store.setFilter('category', value || null)
  }

  const filterByProtected = (value: string) => {
    setSelectedProtected(value)
    const protectedValue = value === 'true' ? true : value === 'false' ? false : null
    store.setFilter('protected', protectedValue)
  }

  const debounceSearch = (value: string) => {
    setSearchQuery(value)
    const timeout = setTimeout(() => {
      store.setFilter('search', value)
    }, 300)
    return () => clearTimeout(timeout)
  }

  const clearAllFilters = () => {
    setSelectedCategory('')
    setSelectedProtected('')
    setSearchQuery('')
    store.clearFilters()
  }

  const viewImage = (image: HarborImage) => {
    setSelectedImage(image)
    setShowViewImageModal(true)
  }

  const remirrorImage = async (image: HarborImage) => {
    if (!confirm(`Re-mirror image ${image.name}:${image.tag} to get the latest version?`)) return

    try {
      const result = await store.remirrorImage(image.id)

      if (result.deployment_id) {
        window.location.href = `/harbor-images/mirror/${result.deployment_id}`
      } else {
        alert('Re-mirror job started successfully')
      }
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || error.message
      alert(`Failed to start re-mirror: ${errorMsg}`)
    }
  }

  const deleteImage = async (image: HarborImage) => {
    if (!confirm(`Are you sure you want to delete ${image.name}:${image.tag}?`)) return

    try {
      await store.deleteImage(image.id)
      await store.fetchImageStats()
    } catch (error: any) {
      alert(`Failed to delete image: ${error.message}`)
    }
  }

  const onImageAdded = async () => {
    setShowAddImageModal(false)
    await store.fetchImages()
    await store.fetchImageStats()
  }

  // Custom Images methods
  const fetchCustomImages = async () => {
    setCustomImagesLoading(true)
    setCustomImagesError(null)
    try {
      const response = await axios.get('/custom-images')
      setCustomImages(response.data.builds || [])
    } catch (error: any) {
      setCustomImagesError(error.response?.data?.detail || 'Failed to fetch custom images')
    } finally {
      setCustomImagesLoading(false)
    }
  }

  const editDockerfile = async (image: CustomImage) => {
    try {
      const response = await axios.get(`/custom-images/${image.id}/editor-url`)
      window.open(response.data.editor_url, '_blank')
    } catch (error: any) {
      alert(`Failed to get editor URL: ${error.response?.data?.detail || error.message}`)
    }
  }

  const buildImage = async (image: CustomImage) => {
    try {
      const response = await axios.post(`/custom-images/${image.id}/build`, {})

      setSelectedImageName(image.name)

      if (response.data.websocket_url) {
        buildExecutorRef.current?.startExecution(`/api/v1${response.data.websocket_url}`)
      } else {
        buildExecutorRef.current?.startExecution(`/api/v1/ws/custom-images/build/${response.data.build_id}`)
      }
    } catch (error: any) {
      alert(`Failed to start build: ${error.response?.data?.detail || error.message}`)
    }
  }

  const toggleBaseStatus = async (image: CustomImage) => {
    try {
      const response = await axios.patch(`/custom-images/${image.id}/toggle-base`)

      image.is_base = response.data.is_base

      alert(`Image ${image.is_base ? 'marked as' : 'removed as'} base image`)

      await fetchCustomImages()
    } catch (error: any) {
      console.error('Failed to toggle base status:', error)
      alert('Failed to toggle base status')
    }
  }

  const editMirroredTemplate = async (image: HarborImage) => {
    try {
      const response = await axios.get(`/harbor/images/${image.id}/edit-template`)
      window.open(response.data.editor_url, '_blank')
    } catch (error: any) {
      console.error('Failed to open template editor:', error)
      alert(`Failed to open template editor: ${error.response?.data?.detail || error.message}`)
    }
  }

  const editCustomTemplate = async (image: CustomImage) => {
    try {
      const response = await axios.get(`/custom-images/${image.id}/edit`)
      window.open(response.data.editor_url, '_blank')
    } catch (error: any) {
      console.error('Failed to open template editor:', error)
      alert(`Failed to open template editor: ${error.response?.data?.detail || error.message}`)
    }
  }

  const toggleMirroredBaseStatus = async (image: HarborImage) => {
    try {
      const wasBase = image.is_base
      const response = await axios.patch(`/harbor/images/${image.id}/toggle-base`)

      image.is_base = response.data.image.is_base

      alert(`Image ${wasBase ? 'removed as' : 'marked as'} base image`)

      await store.fetchImages()
    } catch (error: any) {
      console.error('Failed to toggle base status:', error)
      alert(`Failed to toggle base status: ${error.response?.data?.detail || error.message}`)
    }
  }

  const viewLogs = async (image: CustomImage) => {
    if (!image.output || !image.output.startsWith('/')) {
      alert('No log file available')
      return
    }

    try {
      const logFilePath = image.output
      const filename = logFilePath.split('/').pop()

      const response = await axios.get(`/custom-images/${image.id}/logs/${filename}`, {
        responseType: 'text'
      })

      setLogsImageName(image.name)
      setLogsFilePath(logFilePath)
      setLogsContent(response.data)
      setShowLogsModal(true)
    } catch (error: any) {
      console.error('Failed to fetch logs:', error)
      alert(`Failed to fetch logs: ${error.response?.data?.detail || error.message}`)
    }
  }

  const deleteCustomImage = async (image: CustomImage) => {
    if (!confirm(`Are you sure you want to delete custom image "${image.name}"?`)) return

    try {
      await axios.delete(`/custom-images/${image.id}`)
      await fetchCustomImages()
    } catch (error: any) {
      alert(`Failed to delete image: ${error.response?.data?.detail || error.message}`)
    }
  }

  const onCustomImageCreated = async () => {
    setShowCreateCustomImageModal(false)
    await fetchCustomImages()
  }

  // Watch for tab changes
  useEffect(() => {
    if (activeTab === 'custom') {
      fetchCustomImages()
    }
  }, [activeTab])

  // Lifecycle
  useEffect(() => {
    store.fetchImages()
    store.fetchImageStats()
  }, [])

  const getStatusVariant = (status: string) => {
    switch (status) {
      case 'pending': return 'default'
      case 'building': return 'warning'
      case 'success': return 'success'
      case 'failed': return 'destructive'
      default: return 'default'
    }
  }

  const getCategoryVariant = (category: string) => {
    switch (category) {
      case 'system': return 'default'
      case 'user': return 'warning'
      default: return 'default'
    }
  }

  return (
    <div className="min-h-screen bg-background p-8" /* @allowed-inline */>
      <TkCard className="mb-6">
        <TkCardContent>
          <h1 className="text-2xl font-bold">Thinkube Registry Images</h1>
          <p className="text-muted-foreground mt-1">
            Manage container images in Thinkube registry
          </p>
          <div className="flex gap-2 mt-4" /* @allowed-inline */>
            {activeTab === 'mirrored' && (
              <>
                <TkButton
                  variant="secondary"
                  onClick={syncImages}
                  disabled={store.loading}
                >
                  <RefreshCw className="h-5 w-5 mr-1" />
                  Sync with Registry
                </TkButton>
                <TkButton onClick={() => setShowAddImageModal(true)}>
                  <Plus className="h-5 w-5 mr-1" />
                  Add Image
                </TkButton>
              </>
            )}
            {activeTab === 'custom' && (
              <TkButton onClick={() => setShowCreateCustomImageModal(true)}>
                <Plus className="h-5 w-5 mr-1" />
                Create Custom Image
              </TkButton>
            )}
          </div>
        </TkCardContent>
      </TkCard>

      <TkTabs value={activeTab} onValueChange={(value) => setActiveTab(value as 'mirrored' | 'custom')}>
        <TkTabs.List className="mb-6">
          <TkTabs.Trigger value="mirrored">Mirrored Images</TkTabs.Trigger>
          <TkTabs.Trigger value="custom">Custom Images</TkTabs.Trigger>
        </TkTabs.List>

        <TkTabs.Content value="mirrored">
          <div className="grid md:grid-cols-4 gap-4 mb-6" /* @allowed-inline */>
            <TkStatCard
              title="Total Images"
              value={store.stats?.total?.toString() || '0'}
              valueClassName="text-primary"
            />
            <TkStatCard
              title="System Images"
              value={store.stats?.by_category?.system?.toString() || '0'}
              description="Protected from deletion"
              valueClassName="text-info"
            />
            <TkStatCard
              title="Built Images"
              value={store.stats?.by_category?.custom?.toString() || '0'}
              description="Custom built images"
              valueClassName="text-success"
            />
            <TkStatCard
              title="User Images"
              value={store.stats?.by_category?.user?.toString() || '0'}
              description="Manually added"
              valueClassName="text-warning"
            />
          </div>

          <TkCard className="mb-4">
            <TkCardContent>
              <div className="flex flex-wrap gap-2" /* @allowed-inline */>
                <TkSelect value={selectedCategory} onValueChange={filterByCategory}>
                  <TkSelectTrigger className="w-[200px]">
                    <TkSelectValue placeholder="All Categories" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    <TkSelectItem value="">All Categories</TkSelectItem>
                    <TkSelectItem value="system">System</TkSelectItem>
                    <TkSelectItem value="user">User</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>

                <TkSelect value={selectedProtected} onValueChange={filterByProtected}>
                  <TkSelectTrigger className="w-[200px]">
                    <TkSelectValue placeholder="All Images" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    <TkSelectItem value="">All Images</TkSelectItem>
                    <TkSelectItem value="true">Protected Only</TkSelectItem>
                    <TkSelectItem value="false">Unprotected Only</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>

                <TkInput
                  value={searchQuery}
                  onChange={(e) => debounceSearch(e.target.value)}
                  placeholder="Search images..."
                  className="w-[300px]"
                />

                {hasActiveFilters && (
                  <TkButton variant="ghost" onClick={clearAllFilters}>
                    Clear Filters
                  </TkButton>
                )}
              </div>
            </TkCardContent>
          </TkCard>

          {store.loading && (
            <TkCard>
              <TkCardContent>
                <div className="flex justify-center py-8" /* @allowed-inline */>
                  <TkLoader size="lg" />
                </div>
              </TkCardContent>
            </TkCard>
          )}

          {!store.loading && store.error && (
            <TkErrorAlert message={store.error} className="mb-4" />
          )}

          {!store.loading && !store.error && store.images && store.images.length > 0 && (
            <TkCard>
              <TkCardContent>
                <TkTable>
                  <TkTableHeader>
                    <TkTableRow>
                      <TkTableHead>Image Name</TkTableHead>
                      <TkTableHead>Tag</TkTableHead>
                      <TkTableHead>Category</TkTableHead>
                      <TkTableHead>Description</TkTableHead>
                      <TkTableHead>Mirror Date</TkTableHead>
                      <TkTableHead>Status</TkTableHead>
                      <TkTableHead>Actions</TkTableHead>
                    </TkTableRow>
                  </TkTableHeader>
                  <TkTableBody>
                    {store.images.map((image: HarborImage) => (
                      <TkTableRow key={image.id}>
                        <TkTableCell>
                          <div className="font-medium">{image.repository || image.name}</div>
                        </TkTableCell>
                        <TkTableCell>
                          <div className="text-sm font-mono">{image.tag}</div>
                          {image.digest && (
                            <div className="text-xs text-muted-foreground truncate max-w-xs" title={image.digest}>
                              {image.digest.substring(0, 12)}...
                            </div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex gap-1" /* @allowed-inline */>
                            <TkBadge variant={getCategoryVariant(image.category)}>
                              {image.category}
                              {image.protected && <Lock className="h-3 w-3 ml-1" />}
                            </TkBadge>
                            {image.source && (
                              <TkBadge variant="outline" size="sm">
                                {image.source}
                              </TkBadge>
                            )}
                            {image.is_base && (
                              <TkBadge size="sm">
                                Base
                              </TkBadge>
                            )}
                          </div>
                        </TkTableCell>
                        <TkTableCell>
                          <div className="max-w-xs truncate" title={image.description}>
                            {image.description || '-'}
                          </div>
                        </TkTableCell>
                        <TkTableCell>
                          <div className="text-sm">
                            {formatDate(image.mirror_date)}
                          </div>
                        </TkTableCell>
                        <TkTableCell>
                          {image.vulnerabilities && Object.keys(image.vulnerabilities).length > 0 ? (
                            <div className="flex gap-1" /* @allowed-inline */>
                              {image.vulnerabilities.critical > 0 && (
                                <TkBadge variant="destructive" size="sm">
                                  {image.vulnerabilities.critical} critical
                                </TkBadge>
                              )}
                              {image.vulnerabilities.high > 0 && (
                                <TkBadge variant="warning" size="sm">
                                  {image.vulnerabilities.high} high
                                </TkBadge>
                              )}
                            </div>
                          ) : (
                            <div className="text-sm text-muted-foreground">Not scanned</div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex gap-1" /* @allowed-inline */>
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => viewImage(image)}
                              title="View details"
                            >
                              <Eye className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => toggleMirroredBaseStatus(image)}
                              title={image.is_base ? 'Remove as base image' : 'Mark as base image'}
                              className={image.is_base ? 'text-primary' : ''}
                            >
                              <Box className="h-4 w-4" />
                            </TkButton>
                            {image.is_base && (
                              <TkButton
                                variant="ghost"
                                size="sm"
                                onClick={() => editMirroredTemplate(image)}
                                title="Edit template for this base image"
                              >
                                <Edit2 className="h-4 w-4" />
                              </TkButton>
                            )}
                            {image.category === 'user' && image.tag === 'latest' && (
                              <TkButton
                                variant="ghost"
                                size="sm"
                                onClick={() => remirrorImage(image)}
                                title="Re-mirror latest image for updates"
                              >
                                <RefreshCw className="h-4 w-4" />
                              </TkButton>
                            )}
                            {!image.protected && image.category === 'user' && (
                              <TkButton
                                variant="ghost"
                                size="sm"
                                onClick={() => deleteImage(image)}
                                title="Delete image"
                                className="text-destructive"
                              >
                                <Trash2 className="h-4 w-4" />
                              </TkButton>
                            )}
                          </div>
                        </TkTableCell>
                      </TkTableRow>
                    ))}
                  </TkTableBody>
                </TkTable>

                {store.pagination && store.pagination.total > store.pagination.limit && (
                  <div className="flex justify-center mt-4 gap-2" /* @allowed-inline */>
                    <TkButton
                      variant="outline"
                      disabled={currentPage === 1}
                      onClick={() => store.previousPage()}
                    >
                      «
                    </TkButton>
                    <TkButton variant="outline" disabled>
                      Page {currentPage} of {totalPages}
                    </TkButton>
                    <TkButton
                      variant="outline"
                      disabled={currentPage === totalPages}
                      onClick={() => store.nextPage()}
                    >
                      »
                    </TkButton>
                  </div>
                )}
              </TkCardContent>
            </TkCard>
          )}

          {!store.loading && !store.error && (!store.images || store.images.length === 0) && (
            <TkCard>
              <TkCardContent>
                <div className="text-center py-8" /* @allowed-inline */>
                  <AlertCircle className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
                  <p className="text-lg mb-2">No images found</p>
                  <p className="text-muted-foreground mb-4">
                    {hasActiveFilters ? 'Try adjusting your filters' : 'Start by adding an image or syncing with Harbor'}
                  </p>
                  {!hasActiveFilters && (
                    <TkButton onClick={syncImages}>
                      Sync with Registry
                    </TkButton>
                  )}
                </div>
              </TkCardContent>
            </TkCard>
          )}
        </TkTabs.Content>

        <TkTabs.Content value="custom">
          <TkCard className="mb-4">
            <TkCardContent>
              <div className="flex gap-2 flex-wrap" /* @allowed-inline */>
                <TkSelect value={customImageScope} onValueChange={setCustomImageScope}>
                  <TkSelectTrigger className="w-[200px]">
                    <TkSelectValue placeholder="All Scopes" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    <TkSelectItem value="">All Scopes</TkSelectItem>
                    <TkSelectItem value="general">General Purpose</TkSelectItem>
                    <TkSelectItem value="jupyter">Jupyter/Notebook</TkSelectItem>
                    <TkSelectItem value="ml">Machine Learning</TkSelectItem>
                    <TkSelectItem value="webapp">Web Application</TkSelectItem>
                    <TkSelectItem value="database">Database</TkSelectItem>
                    <TkSelectItem value="devtools">Development Tools</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>

                <div className="flex items-center gap-2" /* @allowed-inline */>
                  <TkCheckbox
                    checked={showOnlyBaseImages}
                    onCheckedChange={(checked) => setShowOnlyBaseImages(checked as boolean)}
                    id="base-images-only"
                  />
                  <TkLabel htmlFor="base-images-only">Base Images Only</TkLabel>
                </div>

                <div className="flex items-center gap-2" /* @allowed-inline */>
                  <TkCheckbox
                    checked={showTreeView}
                    onCheckedChange={(checked) => setShowTreeView(checked as boolean)}
                    id="tree-view"
                  />
                  <TkLabel htmlFor="tree-view">Tree View</TkLabel>
                </div>
              </div>
            </TkCardContent>
          </TkCard>

          {customImagesLoading && (
            <TkCard>
              <TkCardContent>
                <div className="flex justify-center py-8" /* @allowed-inline */>
                  <TkLoader size="lg" />
                </div>
              </TkCardContent>
            </TkCard>
          )}

          {customImagesError && (
            <TkErrorAlert message={customImagesError} className="mb-4" />
          )}

          {!customImagesLoading && !customImagesError && showTreeView && filteredCustomImages.length > 0 && (
            <div className="space-y-2">
              {imageTree.map((image) => (
                <TkCard key={image.id}>
                  <TkCardContent>
                    <div className="flex items-center gap-2 mb-1" /* @allowed-inline */>
                      <h3 className="font-semibold text-lg">{image.name}</h3>
                      {image.is_base && <TkBadge size="sm">Base</TkBadge>}
                      <TkBadge variant="outline" size="sm">{image.scope}</TkBadge>
                      <TkBadge variant={getStatusVariant(image.status)} size="sm">
                        {image.status}
                      </TkBadge>
                    </div>
                    {image.build_config?.description && (
                      <p className="text-sm text-muted-foreground mt-1">
                        {image.build_config.description}
                      </p>
                    )}
                    <div className="text-xs text-muted-foreground mt-1">
                      Created {formatDate(image.created_at)}
                    </div>
                    <div className="flex gap-1 mt-2" /* @allowed-inline */>
                      <TkButton
                        variant="ghost"
                        size="sm"
                        onClick={() => editDockerfile(image)}
                        title="Edit Dockerfile"
                      >
                        <Edit2 className="h-4 w-4" />
                      </TkButton>
                      {image.status !== 'building' && (
                        <TkButton
                          variant="ghost"
                          size="sm"
                          onClick={() => buildImage(image)}
                          title="Build image"
                        >
                          <Settings className="h-4 w-4" />
                        </TkButton>
                      )}
                      {image.status === 'success' && (
                        <TkButton
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleBaseStatus(image)}
                          title={image.is_base ? 'Remove as base image' : 'Mark as base image'}
                        >
                          <Star className={image.is_base ? 'h-4 w-4 fill-current' : 'h-4 w-4'} />
                        </TkButton>
                      )}
                      {image.is_base && image.status === 'success' && (
                        <TkButton
                          variant="ghost"
                          size="sm"
                          onClick={() => editCustomTemplate(image)}
                          title="Edit template for this base image"
                        >
                          <Edit2 className="h-4 w-4" />
                        </TkButton>
                      )}
                    </div>

                    {image.children && image.children.length > 0 && (
                      <div className="ml-4 mt-4 border-l-2 border-border pl-4 space-y-2" /* @allowed-inline */>
                        {image.children.map((child) => (
                          <TkCard key={child.id} className="bg-muted/50">
                            <TkCardContent>
                              <div className="flex items-center gap-2" /* @allowed-inline */>
                                <span className="font-medium">{child.name}</span>
                                <TkBadge variant="outline" size="sm">{child.scope}</TkBadge>
                                <TkBadge variant={getStatusVariant(child.status)} size="sm">
                                  {child.status}
                                </TkBadge>
                              </div>
                              <div className="text-xs text-muted-foreground mt-1">
                                Extended from parent • {formatDate(child.created_at)}
                              </div>
                              <div className="flex gap-1 mt-2" /* @allowed-inline */>
                                <TkButton
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => editDockerfile(child)}
                                >
                                  <Edit2 className="h-3 w-3" />
                                </TkButton>
                                {child.status !== 'building' && (
                                  <TkButton
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => buildImage(child)}
                                  >
                                    <Settings className="h-3 w-3" />
                                  </TkButton>
                                )}
                              </div>
                            </TkCardContent>
                          </TkCard>
                        ))}
                      </div>
                    )}
                  </TkCardContent>
                </TkCard>
              ))}
            </div>
          )}

          {!customImagesLoading && !customImagesError && !showTreeView && filteredCustomImages.length > 0 && (
            <TkCard>
              <TkCardContent>
                <TkTable>
                  <TkTableHeader>
                    <TkTableRow>
                      <TkTableHead>Image Name</TkTableHead>
                      <TkTableHead>Scope</TkTableHead>
                      <TkTableHead>Type</TkTableHead>
                      <TkTableHead>Status</TkTableHead>
                      <TkTableHead>Registry URL</TkTableHead>
                      <TkTableHead>Created</TkTableHead>
                      <TkTableHead>Actions</TkTableHead>
                    </TkTableRow>
                  </TkTableHeader>
                  <TkTableBody>
                    {filteredCustomImages.map((image) => (
                      <TkTableRow key={image.id}>
                        <TkTableCell>
                          <div className="font-medium">{image.name}</div>
                          {image.parent_image_id && (
                            <div className="text-xs text-muted-foreground">
                              Extended from parent
                            </div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <TkBadge variant="outline" size="sm">{image.scope || 'general'}</TkBadge>
                        </TkTableCell>
                        <TkTableCell>
                          {image.is_base ? (
                            <TkBadge size="sm">Base</TkBadge>
                          ) : image.parent_image_id ? (
                            <TkBadge variant="secondary" size="sm">Extended</TkBadge>
                          ) : (
                            <TkBadge variant="outline" size="sm">Standard</TkBadge>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <TkBadge variant={getStatusVariant(image.status)}>
                            {image.status}
                          </TkBadge>
                        </TkTableCell>
                        <TkTableCell>
                          {image.registry_url ? (
                            <div className="text-sm font-mono truncate max-w-xs" title={image.registry_url}>
                              {image.registry_url}
                            </div>
                          ) : (
                            <div className="text-muted-foreground">-</div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <div className="text-sm">{formatDate(image.created_at)}</div>
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex gap-1" /* @allowed-inline */>
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => editDockerfile(image)}
                              title="Edit Dockerfile"
                            >
                              <Edit2 className="h-4 w-4" />
                            </TkButton>
                            {image.status !== 'building' && (
                              <TkButton
                                variant="ghost"
                                size="sm"
                                onClick={() => buildImage(image)}
                                title="Build image"
                              >
                                <Settings className="h-4 w-4" />
                              </TkButton>
                            )}
                            {image.output && (
                              <TkButton
                                variant="ghost"
                                size="sm"
                                onClick={() => viewLogs(image)}
                                title="View logs"
                              >
                                <FileText className="h-4 w-4" />
                              </TkButton>
                            )}
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => deleteCustomImage(image)}
                              title="Delete image"
                              className="text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </TkButton>
                          </div>
                        </TkTableCell>
                      </TkTableRow>
                    ))}
                  </TkTableBody>
                </TkTable>
              </TkCardContent>
            </TkCard>
          )}

          {!customImagesLoading && !customImagesError && filteredCustomImages.length === 0 && (
            <TkCard>
              <TkCardContent>
                <div className="text-center py-8" /* @allowed-inline */>
                  <Box className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
                  <p className="text-lg mb-2">No custom images yet</p>
                  <p className="text-muted-foreground mb-4">
                    Create your first custom Docker image
                  </p>
                  <TkButton onClick={() => setShowCreateCustomImageModal(true)}>
                    Create Custom Image
                  </TkButton>
                </div>
              </TkCardContent>
            </TkCard>
          )}
        </TkTabs.Content>
      </TkTabs>

      <AddImageModal
        open={showAddImageModal}
        onOpenChange={setShowAddImageModal}
        onImageAdded={onImageAdded}
      />

      <ViewImageModal
        open={showViewImageModal}
        onOpenChange={setShowViewImageModal}
        image={selectedImage}
      />

      <CreateCustomImageModal
        open={showCreateCustomImageModal}
        onOpenChange={setShowCreateCustomImageModal}
        onImageCreated={onCustomImageCreated}
      />

      <BuildExecutor
        ref={buildExecutorRef}
        title={`Building ${selectedImageName}`}
        successMessage={`Build complete! Your image ${selectedImageName} is now available.`}
      />

      <TkDialogRoot open={showLogsModal} onOpenChange={setShowLogsModal}>
        <TkDialogContent className="max-w-4xl">
          <TkDialogHeader>
            <TkDialogTitle>{logsImageName} - Build Logs</TkDialogTitle>
          </TkDialogHeader>
          <div>
            <p className="text-sm text-muted-foreground mb-2">Log file: {logsFilePath}</p>
            <TkCodeBlock code={logsContent} language="log" maxHeight="400px" />
          </div>
          <TkDialogFooter>
            <TkButton onClick={() => setShowLogsModal(false)}>Close</TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </div>
  )
}
