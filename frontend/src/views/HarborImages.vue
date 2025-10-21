<template>
  <div class="container mx-auto p-4">
    <!-- Header -->
    <div class="flex justify-between items-center mb-6">
      <div>
        <h1 class="font-bold">Thinkube Registry Images</h1>
        <p class="text-base-content/70 mt-1">
          Manage container images in Thinkube registry
        </p>
      </div>
      <div class="flex gap-2">
        <button
          v-if="activeTab === 'mirrored'"
          @click="syncImages"
          class="btn btn-secondary"
          :disabled="store.loading"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Sync with Registry
        </button>
        <button
          v-if="activeTab === 'mirrored'"
          @click="showAddImageModal = true"
          class="btn btn-primary"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          Add Image
        </button>
        <button
          v-if="activeTab === 'custom'"
          @click="showCreateCustomImageModal = true"
          class="btn btn-primary"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          Create Custom Image
        </button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tabs tabs-lift tabs-lg mb-6">
      <a
        class="tab"
        :class="{ 'tab-active': activeTab === 'mirrored' }"
        @click="activeTab = 'mirrored'"
      >
        Mirrored Images
      </a>
      <a
        class="tab"
        :class="{ 'tab-active': activeTab === 'custom' }"
        @click="activeTab = 'custom'"
      >
        Custom Images
      </a>
    </div>

    <!-- Mirrored Images Tab Content -->
    <div v-if="activeTab === 'mirrored'">
      <!-- Statistics Cards -->
      <div class="stats stats-vertical lg:stats-horizontal shadow w-full mb-6">
        <div class="stat">
          <div class="stat-title">Total Images</div>
          <div class="stat-value text-primary">{{ store.stats.total }}</div>
        </div>
        <div class="stat">
          <div class="stat-title">System Images</div>
          <div class="stat-value text-info">{{ store.stats.by_category.system }}</div>
          <div class="stat-desc">Protected from deletion</div>
        </div>
        <div class="stat">
          <div class="stat-title">Built Images</div>
          <div class="stat-value text-success">{{ store.stats.by_category.custom }}</div>
          <div class="stat-desc">Custom built images</div>
        </div>
        <div class="stat">
          <div class="stat-title">User Images</div>
          <div class="stat-value text-warning">{{ store.stats.by_category.user }}</div>
          <div class="stat-desc">Manually added</div>
        </div>
      </div>

    <!-- Filters -->
    <div class="flex flex-wrap gap-2 mb-4">
      <select
        v-model="selectedCategory"
        @change="filterByCategory"
        class="select select-bordered"
      >
        <option value="">All Categories</option>
        <option value="system">System</option>
        <option value="user">User</option>
      </select>

      <select
        v-model="selectedProtected"
        @change="filterByProtected"
        class="select select-bordered"
      >
        <option value="">All Images</option>
        <option value="true">Protected Only</option>
        <option value="false">Unprotected Only</option>
      </select>

      <div class="form-control">
        <input
          v-model="searchQuery"
          @input="debounceSearch"
          type="text"
          placeholder="Search images..."
          class="input input-bordered"
        />
      </div>

      <button
        v-if="hasActiveFilters"
        @click="clearAllFilters"
        class="btn btn-ghost"
      >
        Clear Filters
      </button>
    </div>

    <!-- Loading State -->
    <div v-if="store.loading" class="flex justify-center py-8">
      <span class="loading loading-spinner loading-lg"></span>
    </div>

    <!-- Error State -->
    <div v-else-if="store.error" class="alert alert-error mb-4">
      <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ store.error }}</span>
    </div>

    <!-- Images Table -->
    <div v-else-if="store.images.length > 0" class="overflow-x-auto">
      <table class="table table-zebra">
        <thead>
          <tr>
            <th>Image Name</th>
            <th>Tag</th>
            <th>Category</th>
            <th>Description</th>
            <th>Mirror Date</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="image in store.images" :key="image.id">
            <td>
              <div class="font-medium">{{ image.repository || image.name }}</div>
            </td>
            <td>
              <div>
                <div class="text-sm font-mono">{{ image.tag }}</div>
                <div v-if="image.digest" class="text-xs text-base-content/70 truncate max-w-xs" :title="image.digest">
                  {{ image.digest.substring(0, 12) }}...
                </div>
              </div>
            </td>
            <td>
              <div class="flex gap-1">
                <div
                  class="badge"
                  :class="{
                    'badge-info': image.category === 'system',
                    'badge-warning': image.category === 'user'
                  }"
                >
                  {{ image.category }}
                  <svg v-if="image.protected" xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 ml-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                </div>
                <div v-if="image.source" class="badge badge-ghost badge-sm">
                  {{ image.source }}
                </div>
                <div v-if="image.is_base" class="badge badge-primary badge-sm">
                  Base
                </div>
              </div>
            </td>
            <td>
              <div class="max-w-xs truncate" :title="image.description">
                {{ image.description || '-' }}
              </div>
            </td>
            <td>
              <div class="text-sm">
                {{ formatDate(image.mirror_date) }}
              </div>
            </td>
            <td>
              <div v-if="image.vulnerabilities && Object.keys(image.vulnerabilities).length > 0" class="flex gap-1">
                <div v-if="image.vulnerabilities.critical > 0" class="badge badge-error badge-sm">
                  {{ image.vulnerabilities.critical }} critical
                </div>
                <div v-if="image.vulnerabilities.high > 0" class="badge badge-warning badge-sm">
                  {{ image.vulnerabilities.high }} high
                </div>
              </div>
              <div v-else class="text-sm text-gray-500">Not scanned</div>
            </td>
            <td>
              <div class="flex gap-1">
                <button
                  @click="viewImage(image)"
                  class="btn btn-ghost btn-xs"
                  title="View details"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                </button>
                <button
                  @click="toggleMirroredBaseStatus(image)"
                  class="btn btn-ghost btn-xs"
                  :title="image.is_base ? 'Remove as base image' : 'Mark as base image'"
                  :class="{ 'text-primary': image.is_base }"
                >
                  <CubeIconSolid v-if="image.is_base" class="h-4 w-4" />
                  <CubeIcon v-else class="h-4 w-4" />
                </button>
                <button
                  v-if="image.is_base"
                  @click="editMirroredTemplate(image)"
                  class="btn btn-ghost btn-xs"
                  title="Edit template for this base image"
                >
                  <PencilSquareIcon class="h-4 w-4" />
                </button>
                <button
                  v-if="image.category === 'user' && image.tag === 'latest'"
                  @click="remirrorImage(image)"
                  class="btn btn-ghost btn-xs"
                  title="Re-mirror latest image for updates"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
                <button
                  v-if="!image.protected && image.category === 'user'"
                  @click="deleteImage(image)"
                  class="btn btn-ghost btn-xs text-error"
                  title="Delete image"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Pagination -->
      <div v-if="store.pagination.total > store.pagination.limit" class="flex justify-center mt-4">
        <div class="join">
          <button
            class="join-item btn"
            :disabled="currentPage === 1"
            @click="store.previousPage()"
          >
            «
          </button>
          <button class="join-item btn">
            Page {{ currentPage }} of {{ totalPages }}
          </button>
          <button
            class="join-item btn"
            :disabled="currentPage === totalPages"
            @click="store.nextPage()"
          >
            »
          </button>
        </div>
      </div>
    </div>

      <!-- Empty State -->
      <div v-else class="text-center py-8">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 mx-auto text-base-content/30 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <p class="text-lg mb-2">No images found</p>
        <p class="text-base-content/70 mb-4">
          {{ hasActiveFilters ? 'Try adjusting your filters' : 'Start by adding an image or syncing with Harbor' }}
        </p>
        <button
          v-if="!hasActiveFilters"
          @click="syncImages"
          class="btn btn-primary"
        >
          Sync with Registry
        </button>
      </div>
    </div>

    <!-- Custom Images Tab Content -->
    <div v-if="activeTab === 'custom'">
      <!-- Scope Filter -->
      <div class="flex gap-2 mb-4">
        <select
          v-model="customImageScope"
          @change="filterCustomImages"
          class="select select-bordered"
        >
          <option value="">All Scopes</option>
          <option value="general">General Purpose</option>
          <option value="jupyter">Jupyter/Notebook</option>
          <option value="ml">Machine Learning</option>
          <option value="webapp">Web Application</option>
          <option value="database">Database</option>
          <option value="devtools">Development Tools</option>
        </select>

        <label class="flex items-center gap-2">
          <input
            type="checkbox"
            v-model="showOnlyBaseImages"
            @change="filterCustomImages"
            class="checkbox"
          />
          <span>Base Images Only</span>
        </label>

        <label class="flex items-center gap-2">
          <input
            type="checkbox"
            v-model="showTreeView"
            class="checkbox"
          />
          <span>Tree View</span>
        </label>
      </div>

      <!-- Custom Images List -->
      <div v-if="customImagesLoading" class="flex justify-center py-8">
        <span class="loading loading-spinner loading-lg"></span>
      </div>

      <div v-else-if="customImagesError" class="alert alert-error mb-4">
        <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>{{ customImagesError }}</span>
      </div>

      <!-- Tree View -->
      <div v-else-if="showTreeView && filteredCustomImages.length > 0" class="space-y-2">
        <div v-for="image in imageTree" :key="image.id" class="card bg-base-100 shadow-sm">
          <div class="card-body p-4">
            <div class="flex items-start justify-between">
              <div class="flex-1">
                <div class="flex items-center gap-2">
                  <h3 class="font-semibold text-lg">{{ image.name }}</h3>
                  <div v-if="image.is_base" class="badge badge-primary badge-sm">Base</div>
                  <div class="badge badge-outline badge-sm">{{ image.scope }}</div>
                  <div
                    class="badge badge-sm"
                    :class="{
                      'badge-info': image.status === 'pending',
                      'badge-warning': image.status === 'building',
                      'badge-success': image.status === 'success',
                      'badge-error': image.status === 'failed'
                    }"
                  >
                    {{ image.status }}
                  </div>
                </div>
                <p v-if="image.build_config?.description" class="text-sm text-base-content/70 mt-1">
                  {{ image.build_config.description }}
                </p>
                <div class="text-xs text-base-content/50 mt-1">
                  Created {{ formatDate(image.created_at) }}
                </div>
              </div>
              <div class="flex gap-1">
                <button
                  @click="editDockerfile(image)"
                  class="btn btn-ghost btn-sm"
                  title="Edit Dockerfile"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </button>
                <button
                  v-if="image.status !== 'building'"
                  @click="buildImage(image)"
                  class="btn btn-ghost btn-sm"
                  title="Build image"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                </button>
                <button
                  v-if="image.status === 'success'"
                  @click="toggleBaseStatus(image)"
                  class="btn btn-ghost btn-sm"
                  :title="image.is_base ? 'Remove as base image' : 'Mark as base image'"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" :fill="image.is_base ? 'currentColor' : 'none'" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                  </svg>
                </button>
                <button
                  v-if="image.is_base && image.status === 'success'"
                  @click="editCustomTemplate(image)"
                  class="btn btn-ghost btn-sm"
                  title="Edit template for this base image"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </button>
              </div>
            </div>

            <!-- Children (nested) -->
            <div v-if="image.children && image.children.length > 0" class="ml-4 mt-4 border-l-2 border-base-300 pl-4 space-y-2">
              <div v-for="child in image.children" :key="child.id" class="relative">
                <div class="absolute -left-6 top-3 w-4 h-0.5 bg-base-300"></div>
                <div class="card bg-base-200/50">
                  <div class="card-body p-3">
                    <div class="flex items-center justify-between">
                      <div>
                        <div class="flex items-center gap-2">
                          <span class="font-medium">{{ child.name }}</span>
                          <div class="badge badge-outline badge-xs">{{ child.scope }}</div>
                          <div
                            class="badge badge-xs"
                            :class="{
                              'badge-info': child.status === 'pending',
                              'badge-warning': child.status === 'building',
                              'badge-success': child.status === 'success',
                              'badge-error': child.status === 'failed'
                            }"
                          >
                            {{ child.status }}
                          </div>
                        </div>
                        <div class="text-xs text-base-content/50 mt-1">
                          Extended from parent • {{ formatDate(child.created_at) }}
                        </div>
                      </div>
                      <div class="flex gap-1">
                        <button @click="editDockerfile(child)" class="btn btn-ghost btn-xs">
                          <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                          </svg>
                        </button>
                        <button v-if="child.status !== 'building'" @click="buildImage(child)" class="btn btn-ghost btn-xs">
                          <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Table View -->
      <div v-else-if="filteredCustomImages.length > 0" class="overflow-x-auto">
        <table class="table table-zebra">
          <thead>
            <tr>
              <th>Image Name</th>
              <th>Scope</th>
              <th>Type</th>
              <th>Status</th>
              <th>Registry URL</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="image in filteredCustomImages" :key="image.id">
              <td>
                <div class="font-medium">{{ image.name }}</div>
                <div v-if="image.parent_image_id" class="text-xs text-base-content/50">
                  Extended from parent
                </div>
              </td>
              <td>
                <div class="badge badge-outline badge-sm">{{ image.scope || 'general' }}</div>
              </td>
              <td>
                <div v-if="image.is_base" class="badge badge-primary badge-sm">Base</div>
                <div v-else-if="image.parent_image_id" class="badge badge-secondary badge-sm">Extended</div>
                <div v-else class="badge badge-ghost badge-sm">Standard</div>
              </td>
              <td>
                <div
                  class="badge"
                  :class="{
                    'badge-info': image.status === 'pending',
                    'badge-warning': image.status === 'building',
                    'badge-success': image.status === 'success',
                    'badge-error': image.status === 'failed'
                  }"
                >
                  {{ image.status }}
                </div>
              </td>
              <td>
                <div v-if="image.registry_url" class="text-sm font-mono truncate max-w-xs" :title="image.registry_url">
                  {{ image.registry_url }}
                </div>
                <div v-else class="text-gray-500">-</div>
              </td>
              <td>
                <div class="text-sm">{{ formatDate(image.created_at) }}</div>
              </td>
              <td>
                <div class="flex gap-1">
                  <button
                    @click="editDockerfile(image)"
                    class="btn btn-ghost btn-xs"
                    title="Edit Dockerfile"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </button>
                  <button
                    v-if="image.status !== 'building'"
                    @click="buildImage(image)"
                    class="btn btn-ghost btn-xs"
                    title="Build image"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                  </button>
                  <button
                    v-if="image.output"
                    @click="viewLogs(image)"
                    class="btn btn-ghost btn-xs"
                    title="View logs"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </button>
                  <button
                    @click="deleteCustomImage(image)"
                    class="btn btn-ghost btn-xs text-error"
                    title="Delete image"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Empty State -->
      <div v-else class="text-center py-8">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 mx-auto text-base-content/30 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        <p class="text-lg mb-2">No custom images yet</p>
        <p class="text-base-content/70 mb-4">
          Create your first custom Docker image
        </p>
        <button
          @click="showCreateCustomImageModal = true"
          class="btn btn-primary"
        >
          Create Custom Image
        </button>
      </div>
    </div>

    <!-- Add Image Modal -->
    <AddImageModal
      v-model="showAddImageModal"
      @image-added="onImageAdded"
    />

    <!-- View Image Modal -->
    <ViewImageModal
      v-model="showViewImageModal"
      :image="selectedImage"
      @close="showViewImageModal = false"
    />

    <!-- Create Custom Image Modal -->
    <CreateCustomImageModal
      v-model="showCreateCustomImageModal"
      @image-created="onCustomImageCreated"
    />

    <!-- Build Executor - EXACTLY like PlaybookExecutor in Templates -->
    <BuildExecutor
      ref="buildExecutor"
      :title="`Building ${selectedImageName}`"
      :successMessage="`Build complete! Your image ${selectedImageName} is now available.`"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useHarborImagesStore } from '@/stores/harborImages'
import axios from 'axios'
import AddImageModal from '@/components/harbor/AddImageModal.vue'
import ViewImageModal from '@/components/harbor/ViewImageModal.vue'
import CreateCustomImageModal from '@/components/harbor/CreateCustomImageModal.vue'
import BuildExecutor from '@/components/harbor/BuildExecutor.vue'
import { CubeIcon, PencilSquareIcon } from '@heroicons/vue/24/outline'
import { CubeIcon as CubeIconSolid } from '@heroicons/vue/24/solid'

const store = useHarborImagesStore()

// Tab state
const activeTab = ref('mirrored')

// Modal states
const showAddImageModal = ref(false)
const showViewImageModal = ref(false)
const showCreateCustomImageModal = ref(false)
const selectedImage = ref(null)
const selectedImageName = ref('')

// Reference to BuildExecutor component - EXACTLY like playbookExecutor in Templates
const buildExecutor = ref(null)

// Custom images state
const customImages = ref([])
const customImagesLoading = ref(false)
const customImagesError = ref(null)
const customImageScope = ref('')
const showOnlyBaseImages = ref(false)
const showTreeView = ref(false)

// Filter states
const selectedCategory = ref('')
const selectedProtected = ref('')
const searchQuery = ref('')
let searchTimeout = null

// Computed
const hasActiveFilters = computed(() => {
  return selectedCategory.value || selectedProtected.value || searchQuery.value
})

// Filtered custom images based on scope and base image filter
const filteredCustomImages = computed(() => {
  let filtered = customImages.value

  if (customImageScope.value) {
    filtered = filtered.filter(img => img.scope === customImageScope.value)
  }

  if (showOnlyBaseImages.value) {
    filtered = filtered.filter(img => img.is_base)
  }

  return filtered
})

// Build image tree for tree view
const imageTree = computed(() => {
  if (!showTreeView.value) return []

  // First, get all root images (no parent)
  const rootImages = filteredCustomImages.value.filter(img => !img.parent_image_id)

  // Build tree structure
  const buildTree = (images) => {
    return images.map(image => {
      const children = filteredCustomImages.value.filter(
        child => child.parent_image_id === image.id
      )
      return {
        ...image,
        children: children.length > 0 ? buildTree(children) : []
      }
    })
  }

  return buildTree(rootImages)
})

const currentPage = computed(() => {
  return Math.floor(store.pagination.skip / store.pagination.limit) + 1
})

const totalPages = computed(() => {
  return Math.ceil(store.pagination.total / store.pagination.limit)
})

// Methods
const formatDate = (dateString) => {
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

const filterByCategory = () => {
  store.setFilter('category', selectedCategory.value || null)
}

const filterByProtected = () => {
  const value = selectedProtected.value === 'true' ? true :
                selectedProtected.value === 'false' ? false : null
  store.setFilter('protected', value)
}

const debounceSearch = () => {
  clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    store.setFilter('search', searchQuery.value)
  }, 300)
}

const clearAllFilters = () => {
  selectedCategory.value = ''
  selectedProtected.value = ''
  searchQuery.value = ''
  store.clearFilters()
}

const viewImage = (image) => {
  selectedImage.value = image
  showViewImageModal.value = true
}

const remirrorImage = async (image) => {
  if (!confirm(`Re-mirror image ${image.name}:${image.tag} to get the latest version?`)) return

  try {
    const result = await store.remirrorImage(image.id)

    // Check if we got a deployment response
    if (result.deployment_id) {
      // Redirect to deployment page for real-time progress
      window.location.href = `/harbor-images/mirror/${result.deployment_id}`
    } else {
      alert('Re-mirror job started successfully')
    }
  } catch (error) {
    const errorMsg = error.response?.data?.detail || error.message
    alert(`Failed to start re-mirror: ${errorMsg}`)
  }
}

const deleteImage = async (image) => {
  if (!confirm(`Are you sure you want to delete ${image.name}:${image.tag}?`)) return

  try {
    await store.deleteImage(image.id)
    await store.fetchImageStats()
  } catch (error) {
    alert(`Failed to delete image: ${error.message}`)
  }
}

const onImageAdded = async () => {
  showAddImageModal.value = false
  await store.fetchImages()
  await store.fetchImageStats()
}

// Custom Images methods
const fetchCustomImages = async () => {
  customImagesLoading.value = true
  customImagesError.value = null
  try {
    const response = await axios.get('/custom-images')
    customImages.value = response.data.builds || []
  } catch (error) {
    customImagesError.value = error.response?.data?.detail || 'Failed to fetch custom images'
  } finally {
    customImagesLoading.value = false
  }
}

const filterCustomImages = () => {
  // The filtering is done reactively through computed properties
  // This method is just a placeholder for the @change event
}

const editDockerfile = async (image) => {
  try {
    const response = await axios.get(`/custom-images/${image.id}/editor-url`)
    window.open(response.data.editor_url, '_blank')
  } catch (error) {
    alert(`Failed to get editor URL: ${error.response?.data?.detail || error.message}`)
  }
}

const buildImage = async (image) => {
  try {
    const response = await axios.post(`/custom-images/${image.id}/build`, {})

    // Set the image name for display
    selectedImageName.value = image.name

    // Start BuildExecutor with WebSocket URL - EXACTLY COPYING template pattern
    if (response.data.websocket_url) {
      buildExecutor.value?.startExecution(`/api/v1${response.data.websocket_url}`)
    } else {
      buildExecutor.value?.startExecution(`/api/v1/ws/custom-images/build/${response.data.build_id}`)
    }
  } catch (error) {
    alert(`Failed to start build: ${error.response?.data?.detail || error.message}`)
  }
}

const toggleBaseStatus = async (image) => {
  try {
    const response = await axios.patch(`/custom-images/${image.id}/toggle-base`)

    // Update the local image data
    image.is_base = response.data.is_base

    showNotification(
      `Image ${image.is_base ? 'marked as' : 'removed as'} base image`,
      'success'
    )

    // Refresh the custom images list
    await loadCustomImages()
  } catch (error) {
    console.error('Failed to toggle base status:', error)
    showNotification('Failed to toggle base status', 'error')
  }
}

const editMirroredTemplate = async (image) => {
  try {
    const response = await axios.get(`/harbor/images/${image.id}/edit-template`)
    window.open(response.data.editor_url, '_blank')
  } catch (error) {
    console.error('Failed to open template editor:', error)
    alert(`Failed to open template editor: ${error.response?.data?.detail || error.message}`)
  }
}

const editCustomTemplate = async (image) => {
  try {
    const response = await axios.get(`/custom-images/${image.id}/edit`)
    window.open(response.data.editor_url, '_blank')
  } catch (error) {
    console.error('Failed to open template editor:', error)
    alert(`Failed to open template editor: ${error.response?.data?.detail || error.message}`)
  }
}

const toggleMirroredBaseStatus = async (image) => {
  try {
    const wasBase = image.is_base
    const response = await axios.patch(`/harbor/images/${image.id}/toggle-base`)

    // Update the local image data - backend returns response.data.image
    image.is_base = response.data.image.is_base

    alert(`Image ${wasBase ? 'removed as' : 'marked as'} base image`)

    // Refresh the images list
    await store.fetchImages()
  } catch (error) {
    console.error('Failed to toggle base status:', error)
    alert(`Failed to toggle base status: ${error.response?.data?.detail || error.message}`)
  }
}

const viewLogs = async (image) => {
  // The output field contains the log file path, fetch the actual content
  if (!image.output || !image.output.startsWith('/')) {
    // Old format or no logs
    alert('No log file available')
    return
  }

  try {
    // Extract filename from path
    const logFilePath = image.output
    const filename = logFilePath.split('/').pop()

    // Fetch log file content from server - EXACTLY like templates do
    const response = await axios.get(`/custom-images/${image.id}/logs/${filename}`, {
      responseType: 'text'
    })

    // Show the logs in modal
    const modal = document.createElement('div')
    modal.className = 'modal modal-open'
    modal.innerHTML = `
      <div class="modal-box max-w-4xl">
        <h3 class="font-bold text-lg mb-4">${image.name} - Build Logs</h3>
        <div class="text-sm text-gray-600 mb-2">Log file: ${logFilePath}</div>
        <pre class="bg-base-200 p-4 rounded-lg overflow-auto max-h-96 text-sm font-mono">${response.data}</pre>
        <div class="modal-action">
          <button class="btn" onclick="this.closest('.modal').remove()">Close</button>
        </div>
      </div>
      <div class="modal-backdrop bg-black/50" onclick="this.closest('.modal').remove()"></div>
    `
    document.body.appendChild(modal)
  } catch (error) {
    console.error('Failed to fetch logs:', error)
    alert(`Failed to fetch logs: ${error.response?.data?.detail || error.message}`)
  }
}

const deleteCustomImage = async (image) => {
  if (!confirm(`Are you sure you want to delete custom image "${image.name}"?`)) return

  try {
    await axios.delete(`/custom-images/${image.id}`)
    await fetchCustomImages()
  } catch (error) {
    alert(`Failed to delete image: ${error.response?.data?.detail || error.message}`)
  }
}

const onCustomImageCreated = async () => {
  showCreateCustomImageModal.value = false
  await fetchCustomImages()
}

// Watch for tab changes
watch(activeTab, (newTab) => {
  if (newTab === 'custom') {
    fetchCustomImages()
  }
})

// Lifecycle
onMounted(async () => {
  await store.fetchImages()
  await store.fetchImageStats()
})
</script>