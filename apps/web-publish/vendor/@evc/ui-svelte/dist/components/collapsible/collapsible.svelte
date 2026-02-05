<script lang="ts">
  import { Collapsible as CollapsiblePrimitive } from 'bits-ui';

  type Props = CollapsiblePrimitive.RootProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<CollapsiblePrimitive.Root {...restProps}>
  {@render children?.()}
</CollapsiblePrimitive.Root>
