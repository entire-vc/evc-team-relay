<script lang="ts">
  import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';

  type Props = ContextMenuPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<ContextMenuPrimitive.Root {...restProps}>
  {@render children?.()}
</ContextMenuPrimitive.Root>
