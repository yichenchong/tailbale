import { useCallback } from "react"

import { usePagination, type UsePaginationOptions, type UsePaginationResult } from "@/lib/usePagination"
import { useResource, type UseResourceOptions, type UseResourceResult } from "@/lib/useResource"

export interface PaginatedLoadParams {
  limit: number
  offset: number
}

export interface UsePaginatedResourceOptions<TData, TItem> extends UsePaginationOptions {
  load: (params: PaginatedLoadParams) => Promise<TData>
  getItems: (data: TData) => readonly TItem[]
  mapError?: UseResourceOptions<TData>["mapError"]
}

export interface UsePaginatedResourceResult<TData, TItem> extends UseResourceResult<TData>, UsePaginationResult {
  items: readonly TItem[]
}

export function usePaginatedResource<TData extends { total: number }, TItem>({
  load,
  getItems,
  mapError,
  ...paginationOptions
}: UsePaginatedResourceOptions<TData, TItem>): UsePaginatedResourceResult<TData, TItem> {
  const pagination = usePagination(paginationOptions)
  const { offset, limit, setTotal, clampToContent, setOffset } = pagination

  const fetcher = useCallback(
    () => load({ limit, offset }),
    [load, limit, offset],
  )

  const onData = useCallback(
    (data: TData): boolean => {
      setTotal(data.total)
      const clamped = clampToContent(data.total, getItems(data).length)
      if (clamped !== null) {
        setOffset(clamped)
        return true
      }
      return false
    },
    [setTotal, clampToContent, getItems, setOffset],
  )

  const resource = useResource(fetcher, { onData, mapError })
  return {
    ...pagination,
    ...resource,
    items: resource.data ? getItems(resource.data) : [],
  }
}
